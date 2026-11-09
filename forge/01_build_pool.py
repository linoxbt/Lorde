#!/usr/bin/env python
"""Step 1: build the judge pool and the downstream sets.

Three phases, each resumable from data/cache/:

  --phase teacher   (teacher on :8000) sample aya_dataset prompts, chunk Wikipedia,
                    generate QA pairs with CuratorKIT's QA stage, translate answers
                    for the wrong-language variant
  --phase student   (student on :8001) Tiny Aya Global answers: student variant for
                    reward items, closed-book answers for grounding items
  --phase assemble  (no GPU) write data/pool.jsonl, data/downstream_raw.jsonl,
                    data/downstream_eval.jsonl

Every prompt appears twice in the pool: once good (gold) and once degraded.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, jsonl, llm, variants  # noqa: E402

CACHE = config.DATA / "cache"
F_PROMPTS = CACHE / "reward_prompts.jsonl"
F_CHUNKS = CACHE / "chunks.jsonl"
F_QA = CACHE / "qa.jsonl"
F_TRANS = CACHE / "translations.jsonl"
F_STUDENT = CACHE / "student.jsonl"

QA_PROMPT = """You are an expert at creating high-quality question-answer pairs from source text.

Given the following text, generate exactly {num_questions} question-answer pair(s).

Requirements:
- Write every question and every answer in the same language as the source text
- Questions should be diverse (factual, conceptual, analytical)
- Answers MUST be fully grounded in the provided text — do not add external knowledge
- Each answer should be 1-3 sentences
- Questions should be standalone (a reader should understand them without seeing the source)

Source text:
---
{context}
---

Respond in the following JSON format ONLY (no other text):
[
  {{"question": "...", "answer": "..."}}
]"""

TRANSLATE_PROMPT = (
    "Translate the following text into {lang}. Output only the translation, nothing else.\n\n{text}"
)


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------- sources


def sample_reward_prompts(n_per_lang: int) -> list[dict]:
    if F_PROMPTS.exists():
        return jsonl.read(F_PROMPTS)
    from datasets import load_dataset

    ds = load_dataset(config.AYA_DATASET, split="train")
    by_code = {v["aya"]: k for k, v in config.LANGS.items()}
    ds = ds.filter(lambda r: r["language_code"] in by_code)
    buckets: dict[str, list[dict]] = defaultdict(list)
    seen: set[str] = set()
    for r in ds:
        inp, tgt = (r["inputs"] or "").strip(), (r["targets"] or "").strip()
        if not (15 <= len(inp) <= 1500 and 20 <= len(tgt) <= 2500):
            continue
        key = _h(inp)
        if key in seen:
            continue
        seen.add(key)
        buckets[by_code[r["language_code"]]].append({"instruction": inp, "gold": tgt, "_k": key})
    rows = []
    for lang in config.LANG_CODES:
        chosen = sorted(buckets[lang], key=lambda r: r["_k"])[:n_per_lang]
        if len(chosen) < n_per_lang:
            print(f"[pool] warning: only {len(chosen)} usable aya_dataset prompts for {lang}")
        for i, r in enumerate(chosen):
            pid = f"r-{lang}-{i:04d}"
            rows.append({"pid": pid, "lang": lang, "instruction": r["instruction"], "gold": r["gold"],
                         "degraded": variants.pick_degraded("reward", pid)})
    jsonl.write(F_PROMPTS, rows)
    print(f"[pool] {len(rows)} reward prompts -> {F_PROMPTS}")
    return rows


def _chunk(text: str, lo: int = 900, hi: int = 2200) -> str | None:
    out = ""
    for para in text.split("\n"):
        para = para.strip()
        if len(para) < 40:
            continue
        if len(out) + len(para) > hi:
            break
        out = f"{out}\n\n{para}" if out else para
        if len(out) >= lo:
            break
    return out if len(out) >= lo * 0.6 else None


ROWS_API = "https://datasets-server.huggingface.co/rows"


def _wiki_rows_api(config_name: str, page: int = 100, max_rows: int = 20000):
    """Articles in dump order from the Hub's datasets-server: fast, no shard downloads."""
    import json as _json
    import time as _time
    import urllib.parse
    import urllib.request

    for offset in range(0, max_rows, page):
        q = urllib.parse.urlencode({"dataset": config.WIKI_DATASET, "config": config_name, "split": "train",
                                    "offset": offset, "length": page})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(f"{ROWS_API}?{q}", timeout=60) as r:
                    rows = _json.load(r).get("rows", [])
                break
            except Exception:  # noqa: BLE001  (rate limits, transient errors)
                if attempt == 4:
                    raise
                _time.sleep(2 ** attempt)
        if not rows:
            return
        for row in rows:
            yield row["row"]


def _wiki_stream(config_name: str):
    from datasets import load_dataset

    yield from load_dataset(config.WIKI_DATASET, config_name, split="train", streaming=True)


def _articles(config_name: str):
    """Rows API first; fall back to `datasets` streaming if the API is unreachable."""
    try:
        yield from _wiki_rows_api(config_name)
    except Exception as e:  # noqa: BLE001
        print(f"[pool] datasets-server unavailable for {config_name} ({e}); streaming instead")
        yield from _wiki_stream(config_name)


def chunk_wikipedia(n_judge: int, n_down_raw: int, n_down_eval: int) -> list[dict]:
    """Disjoint article ranges: judge pool, then downstream raw, then downstream eval."""
    if F_CHUNKS.exists():
        return jsonl.read(F_CHUNKS)
    rows = []
    plan = [("judge", n_judge), ("down_raw", n_down_raw), ("down_eval", n_down_eval)]
    for lang, meta in config.LANGS.items():
        it = _articles(f"{config.WIKI_DUMP}.{meta['wiki']}")
        for use, n in plan:
            got = 0
            while got < n:
                try:
                    art = next(it)
                except StopIteration:
                    break
                title = art.get("title", "")
                if ":" in title or title.lower().startswith(("list of", "liste", "lista")):
                    continue
                if len(art.get("text", "")) < 1500:
                    continue
                c = _chunk(art["text"])
                if not c:
                    continue
                prefix = {"judge": "g", "down_raw": "d", "down_eval": "e"}[use]
                rows.append({"chunk_id": f"{prefix}-{lang}-c{got:04d}", "lang": lang, "use": use,
                             "title": title, "url": art.get("url"), "text": c})
                got += 1
        print(f"[pool] chunked wikipedia {lang}: {dict(Counter(r['use'] for r in rows if r['lang'] == lang))}")
    jsonl.write(F_CHUNKS, rows)
    return rows


# ----------------------------------------------------------------------------- phases


def phase_teacher(args: argparse.Namespace) -> None:
    from curatorkit.generators.qa_generator import QAGenerationTask
    from curatorkit.llm.litellm import LiteLLMBackend
    from curatorkit.schema import DataSample

    prompts = sample_reward_prompts(args.prompts_per_lang)
    chunks = chunk_wikipedia(args.chunks_per_lang, args.down_raw_per_lang, args.down_eval_per_lang)

    # QA pairs through CuratorKIT's QA generation stage, with the teacher.
    backend = LiteLLMBackend(model=args.teacher_model, api_base=args.teacher_base,
                             api_key=os.environ.get("VLLM_API_KEY") or "EMPTY", temperature=0.3,
                             max_tokens=1024, max_retries=2)
    nq = {"judge": 2, "down_raw": 3, "down_eval": 2}
    with jsonl.Appender(F_QA, key="chunk_id") as out:
        todo = [c for c in chunks if c["chunk_id"] not in out]
        print(f"[pool] QA generation: {len(todo)} chunks to go ({len(out.done)} cached)")
        for use in ("judge", "down_raw", "down_eval"):
            batch_all = [c for c in todo if c["use"] == use]
            task = QAGenerationTask(llm=backend, prompt_template=QA_PROMPT, num_questions=nq[use],
                                    concurrency=args.workers)
            for i in range(0, len(batch_all), args.batch):
                batch = batch_all[i: i + args.batch]
                samples = [DataSample(id=c["chunk_id"], source_uri=c.get("url") or c["chunk_id"],
                                      input=c["text"], task_type="instruction_following") for c in batch]
                produced = task.run(samples)
                by_chunk: dict[str, list[dict]] = defaultdict(list)
                for s in produced:
                    by_chunk[s.metadata.get("source_sample_id")].append(
                        {"question": s.instruction, "answer": s.output})
                for c in batch:
                    out.add({"chunk_id": c["chunk_id"], "qas": by_chunk.get(c["chunk_id"], [])[: nq[use]]})
                print(f"[pool] QA {use}: {min(i + args.batch, len(batch_all))}/{len(batch_all)}")

    # Translations for the wrong-language variant.
    ep = llm.Endpoint.parse(f"teacher={args.teacher_model}@{args.teacher_base}")
    with jsonl.Appender(F_TRANS, key="uid") as out:
        reqs = []
        for p in prompts:
            if p["degraded"] != "wrong_language" or p["pid"] in out:
                continue
            target = variants.wrong_language_target(p["lang"], p["pid"], config.LANG_CODES)
            text = TRANSLATE_PROMPT.format(lang=config.LANGS[target]["name"], text=p["gold"])
            reqs.append(llm.Request(uid=p["pid"], messages=[{"role": "user", "content": text}],
                                    params={"temperature": 0.2, "max_tokens": 1024},
                                    meta={"target_lang": target}))
        if reqs:
            llm.run(ep, reqs, concurrency=args.workers, desc="translate",
                    on_result=lambda r: out.add(r.as_row()) if r.text else None)
    print("[pool] teacher phase done")


def _qa_items(chunks: list[dict], qa_rows: list[dict]) -> list[dict]:
    text = {c["chunk_id"]: c for c in chunks}
    items = []
    for row in qa_rows:
        c = text.get(row["chunk_id"])
        if not c:
            continue
        for i, qa in enumerate(row["qas"]):
            if not qa.get("question") or not qa.get("answer"):
                continue
            items.append({"qid": f"{row['chunk_id']}-q{i}", "chunk_id": row["chunk_id"], "lang": c["lang"],
                          "use": c["use"], "source_text": c["text"], "question": qa["question"],
                          "answer": qa["answer"]})
    return items


def downstream_variant(qid: str) -> str:
    x = variants.rng_for("down", qid).random()
    if x < 0.50:
        return "gold"
    if x < 0.70:
        return "closed_book"
    if x < 0.85:
        return "fabricated_numbers"
    if x < 0.95:
        return "swapped_answer"
    return "truncated"


def phase_student(args: argparse.Namespace) -> None:
    prompts = jsonl.read(F_PROMPTS)
    qa = _qa_items(jsonl.read(F_CHUNKS), jsonl.read(F_QA))
    ep = llm.Endpoint.parse(f"student={args.student_model}@{args.student_base}")
    with jsonl.Appender(F_STUDENT, key="uid") as out:
        reqs = []
        for p in prompts:
            if p["degraded"] == "student" and p["pid"] not in out:
                reqs.append(llm.Request(uid=p["pid"], messages=[{"role": "user", "content": p["instruction"]}],
                                        params={"temperature": 0.7, "max_tokens": 512}))
        for q in qa:
            need = (q["use"] == "judge" and variants.pick_degraded("grounding", q["qid"]) == "closed_book") or (
                q["use"] == "down_raw" and downstream_variant(q["qid"]) == "closed_book")
            if need and q["qid"] not in out:
                reqs.append(llm.Request(uid=q["qid"], messages=[{"role": "user", "content": q["question"]}],
                                        params={"temperature": 0.7, "max_tokens": 256}))
        print(f"[pool] student phase: {len(reqs)} requests ({len(out.done)} cached)")
        if reqs:
            llm.run(ep, reqs, concurrency=args.workers, desc="student",
                    on_result=lambda r: out.add(r.as_row()) if r.text else None)
    print("[pool] student phase done")


def phase_assemble(args: argparse.Namespace) -> None:
    prompts = jsonl.read(F_PROMPTS)
    chunks = jsonl.read(F_CHUNKS)
    qa = _qa_items(chunks, jsonl.read(F_QA))
    trans = {r["uid"]: r["raw"] for r in jsonl.read(F_TRANS) if r.get("raw")}
    student = {r["uid"]: r["raw"] for r in jsonl.read(F_STUDENT) if r.get("raw")}

    pool: list[dict] = []
    gold_by_lang: dict[str, list[str]] = defaultdict(list)
    for p in prompts:
        gold_by_lang[p["lang"]].append(p["gold"])

    for p in prompts:
        pid, lang = p["pid"], p["lang"]
        base = {"kind": "reward", "lang": lang, "group": pid, "instruction": p["instruction"]}
        pool.append({"uid": f"{pid}:gold", "variant": "gold", **base, "response": p["gold"]})
        v = p["degraded"]
        resp = None
        if v == "student":
            resp = student.get(pid)
        elif v == "wrong_language":
            resp = trans.get(pid)
        elif v == "truncated":
            resp = variants.truncate(p["gold"], pid)
        elif v == "off_topic":
            resp = variants.swap_from(gold_by_lang[lang], pid, exclude=p["gold"])
        elif v == "padded_repetition":
            resp = variants.pad_repetition(p["gold"], pid)
        if not resp:  # LLM variant missing: fall back to a rule-based degradation
            v, resp = "truncated", variants.truncate(p["gold"], pid)
        pool.append({"uid": f"{pid}:{v}", "variant": v, **base, "response": resp})

    judge_qa = [q for q in qa if q["use"] == "judge"]
    answers_by_lang: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for q in qa:
        answers_by_lang[q["lang"]].append((q["chunk_id"], q["answer"]))

    def other_answer(q: dict) -> str:
        cands = [a for cid, a in answers_by_lang[q["lang"]] if cid != q["chunk_id"]]
        return variants.swap_from(cands, q["qid"], exclude=q["answer"])

    def degrade(q: dict, v: str) -> tuple[str, str]:
        ans = None
        if v == "closed_book":
            ans = student.get(q["qid"])
        elif v == "swapped_answer":
            ans = other_answer(q)
        elif v == "fabricated_numbers":
            ans = variants.fabricate_numbers(q["answer"], q["qid"])
        elif v == "truncated":
            ans = variants.truncate(q["answer"], q["qid"])
        if not ans:
            v, ans = "fabricated_numbers", variants.fabricate_numbers(q["answer"], q["qid"])
        return v, ans

    for q in judge_qa:
        base = {"kind": "grounding", "lang": q["lang"], "group": q["chunk_id"],
                "source_text": q["source_text"], "question": q["question"]}
        pool.append({"uid": f"{q['qid']}:gold", "variant": "gold", **base, "answer": q["answer"]})
        v, ans = degrade(q, variants.pick_degraded("grounding", q["qid"]))
        pool.append({"uid": f"{q['qid']}:{v}", "variant": v, **base, "answer": ans})

    raw, ev = [], []
    for q in qa:
        if q["use"] == "down_raw":
            v = downstream_variant(q["qid"])
            ans = q["answer"] if v == "gold" else None
            if ans is None:
                v, ans = degrade(q, v)
            raw.append({"uid": q["qid"], "instruction": q["question"], "input": q["source_text"],
                        "output": ans, "lang": q["lang"], "chunk_id": q["chunk_id"], "variant": v})
        elif q["use"] == "down_eval":
            ev.append({"uid": q["qid"], "question": q["question"], "context": q["source_text"],
                       "answer": q["answer"], "lang": q["lang"], "chunk_id": q["chunk_id"]})

    n = jsonl.write(config.DATA / "pool.jsonl", pool)
    jsonl.write(config.DATA / "downstream_raw.jsonl", raw)
    jsonl.write(config.DATA / "downstream_eval.jsonl", ev)
    stats = {
        "pool": n,
        "by_kind": Counter(r["kind"] for r in pool),
        "by_lang": Counter(r["lang"] for r in pool),
        "by_variant": Counter(f"{r['kind']}:{r['variant']}" for r in pool),
        "downstream_raw": len(raw),
        "downstream_raw_variants": Counter(r["variant"] for r in raw),
        "downstream_eval": len(ev),
    }
    jsonl.write_json(config.DATA / "pool_stats.json", stats)
    print(f"[pool] wrote {n} pool items, {len(raw)} downstream raw, {len(ev)} downstream eval")
    for k, v in stats.items():
        print(f"  {k}: {dict(v) if isinstance(v, Counter) else v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", required=True, choices=["teacher", "student", "assemble"])
    ap.add_argument("--teacher-model", default="openai/teacher")
    ap.add_argument("--teacher-base", default="http://localhost:8000/v1")
    ap.add_argument("--student-model", default="openai/student")
    ap.add_argument("--student-base", default="http://localhost:8001/v1")
    ap.add_argument("--prompts-per-lang", type=int, default=300)
    ap.add_argument("--chunks-per-lang", type=int, default=60)
    ap.add_argument("--down-raw-per-lang", type=int, default=150)
    ap.add_argument("--down-eval-per-lang", type=int, default=40)
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--batch", type=int, default=64, help="chunks per cached QA batch")
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    {"teacher": phase_teacher, "student": phase_student, "assemble": phase_assemble}[args.phase](args)


if __name__ == "__main__":
    main()
