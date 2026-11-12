#!/usr/bin/env python
"""Fill docs/HF_ARTICLE.md from the template and the measured results.

    python docs/build_article.py [--rows 100000] [--gpu t4|l4]
    LORDE_ROOT=<dir with data/ results/ bench/ runs/> python docs/build_article.py

Every number comes from a result file: results/judge_eval.json (with results/preds/*.meta.json behind it),
bench/results/<judge>.json, results/plugin_eval.json, results/downstream.json, runs/<run>/train_meta.json,
data/splits_summary.json, data/pool.jsonl + data/labels.jsonl (teacher audit) and, for the near-duplicate
check of splits built before it existed, data/sft/train.jsonl + data/test.jsonl. A section whose inputs are
missing says so instead of guessing. The step reports (judge_eval, plugin_eval, downstream) are re-rendered from
their JSON with the step's own renderer, so the article picks up renderer fixes without re-running a GPU step.

Throughput is reported for the GPU it was measured on (judge_eval.json "hardware"); ``--gpu`` only supplies the
hourly price when the run recorded none, and it must match the measured GPU. The API side of the ROI is a
list-price estimate, never a measured bill.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, human, splits  # noqa: E402
from lorde_forge.report import table  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent / "HF_ARTICLE.template.md"
DOCS = config.ROOT / "docs"
CODE = Path(__file__).resolve().parent.parent
CAND = "lorde"
STEP = {"judge_eval": "forge/06_judge_eval.py", "plugin_eval": "forge/07_curatorkit_plugin.py",
        "downstream": "forge/08_downstream.py"}


# ----------------------------------------------------------------------------- inputs


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.open(encoding="utf-8") if line.strip()] if p.exists() else []


def judge_eval() -> dict:
    return _json(config.RESULTS / "judge_eval.json")


def bench(name: str) -> dict:
    return _json(config.ROOT / "bench" / "results" / f"{name}.json")


def downstream() -> dict:
    return _json(config.RESULTS / "downstream.json")


def plugin() -> dict:
    return _json(config.RESULTS / "plugin_eval.json")


def train_meta() -> dict:
    return _json(config.RUNS / config.RUN_NAME / "train_meta.json")


def _load_step(rel: str):
    spec = importlib.util.spec_from_file_location("_step_" + Path(rel).stem, CODE / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------------------- formatting


def pct(v: float | None, nd: int = 1) -> str:
    return "–" if v is None else f"{v * 100:.{nd}f}%"


def kappa(v: float | None) -> str:
    """κ is always printed with three decimals (0.748 is not 0.75)."""
    return "undefined" if v is None else f"{v:.3f}"


def kappa_eq(v: float | None) -> str:
    """"κ = 0.748", or "κ undefined" (one class only, e.g. a judge that passes everything)."""
    return "κ undefined" if v is None else f"κ = {v:.3f}"


def pval(p: float | None) -> str:
    if p is None:
        return "p not computed"
    return f"p = {p:.3f}" if p >= 0.001 else "p < 0.001"


def lang_name(code: str) -> str:
    return config.LANGS.get(code, {}).get("name", code)


def set_name(name: str) -> str:
    """How a downstream training set is called in prose."""
    return {"ungated": "the ungated set", "base": "the untrained base model"}.get(name, f"{name}-gated")


# ----------------------------------------------------------------------------- sections


def section(name: str) -> str:
    """A step report, re-rendered from its JSON by the step's own renderer (falls back to the .md)."""
    js = config.RESULTS / f"{name}.json"
    body = None
    if js.exists():
        try:
            mod = _load_step(STEP[name])
            r = json.loads(js.read_text())
            if name == "judge_eval":
                r = with_cache_meta(r)
            body = mod.render_md(r, CAND) if name == "judge_eval" else mod.render_md(r)
        except Exception as e:  # noqa: BLE001  (an old JSON the renderer cannot read: use the stored md)
            print(f"[article] {name}: re-render failed ({type(e).__name__}: {e}); using results/{name}.md")
    if body is None:
        p = config.RESULTS / f"{name}.md"
        if not p.exists():
            return f"_Pending: run the step that writes results/{name}.md._"
        body = p.read_text()
    return "\n".join("##" + line if line.startswith("#") else line for line in body.splitlines())


def with_cache_meta(r: dict) -> dict:
    """Fill each judge's cache record from results/preds/<judge>.meta.json (reports written before step 6 kept
    legacy_trusted and the stamp date in the report itself)."""
    for name, j in r.get("judges", {}).items():
        meta = _json(config.RESULTS / "preds" / f"{name}.meta.json")
        c = j.get("cache")
        if c is None or not meta:
            continue
        c.setdefault("legacy_trusted", bool(meta.get("legacy_trusted")) or str(c.get("status", "")).startswith("legacy"))
        c.setdefault("stamped", meta.get("written"))
        c.setdefault("model", meta.get("model"))
    return r


def labels_note() -> str:
    has_32b = (config.DATA / "labels_32b.jsonl").exists()
    hl = human.load_labels(config.DATA / "human_labels.jsonl", config.DATA / "human_sample_key.jsonl")
    raw = _read_jsonl(config.DATA / "human_labels.jsonl")
    unblind = sum(not str(r.get("uid", "")).startswith("h-") for r in raw)
    n_sample = len(_read_jsonl(config.DATA / "human_sample.jsonl"))
    parts = ["- **Labels:** Aya Expanse 8B (self-hosted vLLM, fp16 on 2xT4) for every item"]
    parts.append("; Aya Expanse 32B via the Cohere API on the golden-300 calibration slice" if has_32b
                 else "; the planned Aya Expanse 32B calibration slice was not run")
    if hl:
        blind = ("blind: labellers saw neither the item id nor its variant" if not unblind else
                 f"{unblind} of {len(raw)} label rows were made before the label tool was blinded")
        parts.append(f"; {len(hl)} hand-labelled items with the gate's own rubric ({blind}).")
    else:
        parts.append(f"; no hand labels have been collected yet (0 of the {n_sample or 'planned'} blind sample "
                     "items), so human agreement is not measured.")
    return "".join(parts)


def human_status() -> str:
    hl = human.load_labels(config.DATA / "human_labels.jsonl", config.DATA / "human_sample_key.jsonl")
    return f"Measured on {len(hl)} hand-labelled items." if hl else "**Not measured yet: 0 hand labels.**"


def leakage_stats() -> dict | None:
    """Near-duplicate report: from splits_summary.json, else recomputed from the split files when present."""
    ss = _json(config.DATA / "splits_summary.json")
    if ss.get("near_duplicates"):
        return ss["near_duplicates"]
    train_p, test_p, pool_p = config.DATA / "sft" / "train.jsonl", config.DATA / "test.jsonl", config.DATA / "pool.jsonl"
    if not (train_p.exists() and test_p.exists() and pool_p.exists()):
        return None
    pool = {r["uid"]: r for r in _read_jsonl(pool_p)}
    train = [pool[r["uid"]] for r in _read_jsonl(train_p) if r.get("uid") in pool]
    rep = splits.leakage_report(train, _read_jsonl(test_p))
    rep["grouping_threshold"] = None  # these splits were built without near-duplicate grouping
    return rep


def leakage() -> str:
    base = "Splits are grouped by prompt and source passage: no test prompt or passage appears verbatim in training."
    nd = leakage_stats()
    if not nd:
        return base + " Near-duplicates across splits were not measured for this build."
    thr = nd.get("threshold", splits.NEAR_DUP_THRESHOLD)
    s = (f"{base} Near-duplicates do cross the split, though: **{nd['test_prompts_near_train']} of "
         f"{nd['test_prompts']}** distinct test reward prompts have a training prompt at least {thr:.0%} similar "
         f"(normalized text), {nd['test_passages_near_train']} of {nd['test_passages']} test passages do, and "
         f"**{nd['test_gold_responses_identical_to_train']}** test gold responses are identical to a training one "
         "(mostly short answers such as a sentiment label).")
    if not nd.get("grouping_threshold"):
        s += (" These splits were built before the near-duplicate grouping in `forge/03_make_splits.py`; future "
              "builds put near-duplicates in one split.")
    return s


def train_recipe() -> str:
    meta = train_meta()
    r = meta.get("recipe", {})
    epochs = r.get("epochs", "?")
    rows = meta.get("train_rows")
    hw = meta.get("hardware")
    secs = meta.get("train_seconds")
    hours = ""
    if secs:
        on = f" on one {hw}" if hw else ""
        if meta.get("train_seconds_all_sessions"):
            n = meta.get("train_sessions", 1)
            hours = f", {secs / 3600:.1f} h of training{on} over {n} session{'s' if n != 1 else ''}"
        else:
            hours = (f", {secs / 3600:.1f} h of training{on} as recorded in train_meta.json: that is the last "
                     "session only (the code that wrote it did not add earlier sessions of a resumed run), so it "
                     "is a lower bound")
    prec = f"; {meta['precision']}" if meta.get("precision") else ""
    rows_s = f"{rows:,}" if isinstance(rows, int) else "the"
    return (f"LoRA r={r.get('lora_r', 16)}, α={r.get('lora_alpha', 32)}, dropout {r.get('lora_dropout', 0.05)} on "
            f"all attention and MLP projections; lr {r.get('lr', 1e-4)} cosine, {epochs} epoch(s) over {rows_s} "
            f"training rows, effective batch {r.get('batch_size', 1) * r.get('grad_accum', 16)} "
            f"({r.get('batch_size', 1)} x {r.get('grad_accum', 16)} accumulation), max "
            f"{r.get('max_len', config.MAX_SEQ_LEN):,} tokens (longer rows dropped, never truncated){prec}{hours}. "
            "Tiny Aya's 261k-token vocabulary makes the logits the memory peak on a 16 GB T4, hence batch 1.")


def teacher_pass_rates() -> dict[tuple[str, str], list[float]]:
    pool = {r["uid"]: r for r in _read_jsonl(config.DATA / "pool.jsonl")}
    agg: dict[tuple[str, str], list[float]] = {}
    for r in _read_jsonl(config.DATA / "labels.jsonl"):
        p = pool.get(r["uid"])
        if p and r.get("ok") and r.get("score") is not None:
            agg.setdefault((p["kind"], p["variant"]), []).append(float(r["score"]))
    return agg


def _rate(s: list[float]) -> float:
    return sum(x >= config.THRESHOLD for x in s) / len(s)


def self_contradictions() -> tuple[int, int, int]:
    """(passing grounding replies that list an unsupported claim, of which 'grounded' with score 1.0, all
    parseable grounding replies)."""
    g = [r for r in _read_jsonl(config.DATA / "labels.jsonl")
         if r.get("kind") == "grounding" and r.get("ok") and r.get("score") is not None]
    bad = [r for r in g if r["score"] >= config.THRESHOLD and (r.get("parsed") or {}).get("unsupported_claims")]
    worst = [r for r in bad if r["score"] >= 1.0 and str(r.get("verdict") or "").lower() == "grounded"]
    return len(bad), len(worst), len(g)


def lenient_sentence(agg: dict) -> str:
    names = {"swapped_answer": "swapped answers", "fabricated_numbers": "fabricated numbers",
             "closed_book": "closed-book answers", "truncated": "truncated answers"}
    got = [f"{_rate(agg[('grounding', v)]):.0%} of {n}" for v, n in names.items() if agg.get(("grounding", v))]
    return ", ".join(got[:-1]) + (" and " + got[-1] if len(got) > 1 else got[0] if got else "")


def teacher_audit() -> str:
    agg = teacher_pass_rates()
    if not agg:
        return "_Pending: teacher labels (forge/02_teacher_label.py)._"
    rows = [[k, v, len(s), f"{_rate(s):.0%}", f"{sum(s) / len(s):.2f}"] for (k, v), s in sorted(agg.items())]
    out = ["### What the teacher lets through\n",
           "Lorde learns the teacher's decisions, so its blind spots matter. Teacher pass rate (score at least "
           f"{config.THRESHOLD}) by item variant; every non-gold variant is degraded on purpose.\n",
           table(["Gate", "Variant", "Items", "Teacher pass rate", "Mean score"], rows), ""]
    if any(k == "grounding" and v != "gold" for k, v in agg):
        bad, worst, n = self_contradictions()
        out.append(f"**The teacher is a lenient grounding reference.** It passes {lenient_sentence(agg)}, all "
                   "degraded on purpose"
                   + (f", and {bad:,} of its {n:,} parseable grounding replies pass an answer while listing an "
                      f"unsupported claim ({worst} of them say `grounded` with score 1.0)" if bad else "")
                   + ". Decision agreement, κ and verdict accuracy below are measured against this teacher, so "
                   "they measure how faithfully Lorde imitates it, not whether its decisions are right.\n")
    return "\n".join(out)


def missed_criteria() -> list[str]:
    """Every acceptance criterion the candidate does not meet, from the result files."""
    out = []
    for c in judge_eval().get("acceptance") or []:
        if c.get("blocking") and c.get("met") is not True:
            what = "not measured" + (f": {c['reason']}" if c.get("reason") else "") if c.get("met") is None else \
                f"{c.get('value')}"
            out.append(f"{c['criterion']}: {what}")
    for name in (CAND, f"{CAND}+so"):
        b = bench(name)
        for c in b.get("checks") or []:
            if c.get("met") is not True:
                val = ""
                if "decision agreement" in c["criterion"]:
                    val = pct((b.get("vs_teacher_8b") or {}).get("decision_agreement"))
                elif "κ" in c["criterion"]:
                    val = kappa((b.get("vs_teacher_8b") or {}).get("kappa"))
                elif "Adversarial" in c["criterion"]:
                    val = pct(b.get("adversarial_pass_rate"), 0)
                out.append(f"Bench ({name}): {c['criterion']}: {val or 'not met'}")
    d = downstream()
    if d.get("within_2pct_of_teacher") is False:
        out.append(f"Downstream F1 within ±2% of teacher-gated: {d.get('f1_relative_to_teacher', 0) * 100:+.1f}%")
    if d.get("better_than_ungated_p05") is False:
        out.append("Downstream: Lorde-gated F1 better than ungated with p < 0.05: no")
    return out


def short_missed() -> list[str]:
    """The missed criteria in a few words each, for the limitations."""
    out = []
    for c in judge_eval().get("acceptance") or []:
        if c.get("blocking") and c.get("met") is not True:
            name = re.sub(r"\s*\(.*?\)", "", c["criterion"])
            worst = re.search(r"worst: (\w+)", c["criterion"])
            val = "not measured" if c.get("met") is None else str(c.get("value"))
            out.append(f"{name} ({val}{f', {lang_name(worst.group(1))}' if worst and c.get('met') is not None else ''})")
    b = bench(CAND)
    g = b.get("vs_teacher_8b") or {}
    if any(c.get("met") is not True and "Golden" in c["criterion"] for c in b.get("checks") or []):
        out.append(f"golden-300 agreement ≥ 90% and κ ≥ 0.75 ({pct(g.get('decision_agreement'))}, "
                   f"{kappa_eq(g.get('kappa'))})")
    if any(c.get("met") is not True and "Adversarial" in c["criterion"] for c in b.get("checks") or []):
        out.append(f"adversarial pass rate ≤ 5% ({pct(b.get('adversarial_pass_rate'), 0)})")
    return out


def acceptance_md() -> str:
    r = judge_eval()
    crit = r.get("acceptance") or []
    if not crit:
        return ""
    prom = r.get("promotable")
    if prom is None:  # report from before the explicit flag: derive it the same way
        blocking = [c for c in crit if c.get("blocking")]
        prom = bool(blocking) and all(c.get("met") is True for c in blocking)
    missed = missed_criteria()
    head = f"**Promotion verdict (spec 9.2): {'promotable' if prom else 'not promotable'}.**"
    if not missed:
        return head + "\n"
    return head + " Criteria not met or not measured:\n\n" + "\n".join(f"- {m}" for m in missed) + "\n"


def bench_md() -> str:
    rows, adv = [], []
    ev = judge_eval().get("judges", {})
    for name in (CAND, f"{CAND}+so"):
        full = (ev.get(name) or {}).get("vs_teacher")
        b = bench(name)
        g = b.get("vs_teacher_8b")
        if full:
            rows.append([name, f"full held-out test ({full.get('n')})", pct(full.get("gate_usable_rate")),
                         pct(full.get("decision_agreement")), kappa(full.get("kappa")), f"{full.get('mae', 0):.3f}"])
        if g:
            rows.append([name, f"golden-300 ({g.get('n')})", pct(g.get("gate_usable_rate")),
                         pct(g.get("decision_agreement")), kappa(g.get("kappa")), f"{g.get('mae', 0):.3f}"])
        if b.get("adversarial_pass_rate") is not None:
            by = ", ".join(f"{k.replace('_', ' ')} {v:.0%}" for k, v in (b.get("adversarial_by_attack") or {}).items())
            adv.append(f"- **{name}:** passes **{b['adversarial_pass_rate']:.0%}** of adversarial-100 items whose "
                       f"reference is *fail* (target ≤ 5%){f'; by attack: {by}' if by else ''}.")
    if not rows:
        return ""
    out = ["### Lorde Bench: golden-300 and adversarial-100\n",
           "Golden-300 is the balanced Bench subset of the held-out test set (150 reward and 150 grounding items, "
           "drawn round-robin over language and variant), so it weighs the degraded variants more than the full "
           "test set does. Reference: the teacher.\n",
           table(["Judge", "Set (n)", "Gate-usable", "Decision agr.", "κ", "MAE"], rows), ""]
    if adv:
        out += ["Adversarial-100 holds prompt injections, forged verdicts, empty fields, extreme lengths and mixed "
                "scripts:\n", *adv, ""]
    return "\n".join(out)


def downstream_notes() -> str:
    d, p = downstream(), plugin()
    if not d.get("metrics"):
        return ""
    out = ["### Reading the downstream result\n"]
    st = d.get("setup") or driver_downstream_setup()
    rows = sorted(set((d.get("train_rows") or {}).values()))
    if st:
        ep = st.get("epochs", "?")
        out.append(f"- **Small study:** one training run (one seed) per set, {ep} epoch{'' if ep == 1 else 's'}, "
                   f"{', '.join(f'{x:,}' for x in rows) or '?'} rows per set"
                   + (", every set subsampled to the same size" if st.get("equal_size") else "")
                   + f"{' (settings: ' + st['source'] + ')' if st.get('source') else ''}. The F1 differences "
                   "between gated sets need more seeds before they can be called parity.")
    sig = d.get("significance") or {}
    worse = [(k.split(" vs ")[1], s) for k, v in sig.items()
             if (s := v.get("lexical_groundedness")) and s.get("significant") and (s.get("delta") or 0) < 0]
    if worse:
        lj, tj = ((p.get("judges") or {}).get(k) or {} for k in (CAND, "teacher"))
        more = [f"{v.replace('_', ' ')} {lj['pass_rate_by_variant'][v]:.1%} vs {tj['pass_rate_by_variant'][v]:.1%}"
                for v in ("fabricated_numbers", "closed_book")
                if v in (lj.get("pass_rate_by_variant") or {}) and v in (tj.get("pass_rate_by_variant") or {})
                and lj["pass_rate_by_variant"][v] > tj["pass_rate_by_variant"][v]]
        out.append("- **Groundedness regresses significantly:** Lorde-gated answers are less grounded in the source "
                   "than " + " and ".join(f"{set_name(b)} ({s['mean_candidate']:.3f} vs {s['mean_baseline']:.3f}, "
                                          f"{pval(s.get('p_value'))})" for b, s in worse) + "."
                   + (" In the plug-in run Lorde passed more degraded grounding answers than the teacher ("
                      + "; ".join(more) + "), which fits this." if more else ""))
    t = (p.get("judges") or {}).get("teacher") or {}
    perr = sum(v for k, v in (t.get("rejection_reasons") or {}).items() if k.startswith("judge_error"))
    if perr:
        out.append(f"- **The teacher-gated set is depressed by parse errors:** in the plug-in run, {perr:,} of "
                   f"{p.get('n_raw', '?'):,} rows ({t.get('judge_error_rate', 0):.1%}) were rejected because CuratorKIT "
                   "could not parse the teacher's reply, not because the teacher judged them bad. The "
                   "\"teacher-gated\" baseline is therefore the teacher plus its JSON failures.")
    return "\n".join(out) + "\n"


def driver_downstream_setup() -> dict:
    """The downstream settings the Kaggle driver runs (downstream.json from before "setup" was recorded)."""
    try:
        args = shlex.split(_load_step("forge/kaggle_driver.py").DOWNSTREAM_ARGS)
    except Exception:  # noqa: BLE001
        return {}
    st: dict[str, Any] = {"equal_size": "--equal-size" in args, "source": "forge/kaggle_driver.py DOWNSTREAM_ARGS"}
    for flag, k in (("--epochs", "epochs"), ("--max-rows", "max_rows")):
        if flag in args:
            st[k] = int(args[args.index(flag) + 1])
    return st


def cache_note() -> str:
    js = judge_eval().get("judges", {})
    legacy = [n for n, j in js.items() if (j.get("cache") or {}).get("legacy_trusted")
              or str((j.get("cache") or {}).get("status", "")).startswith("legacy")]
    if not legacy:
        return ""
    sha = {str(((js[n].get("cache") or {}).get("model") or {}).get("sha") or "")[:12] for n in legacy} - {""}
    return (f"The step-6 predictions behind the judge numbers ({', '.join(legacy)}) are a cache written by an "
            "earlier Kaggle session, before caches carried a weights fingerprint, and reused with "
            "`--trust-legacy-cache`; the run that reported them stamped them with the weights it served"
            + (f" (`{'`, `'.join(sorted(sha))}`)" if sha else "")
            + ", which does not prove the earlier session served the same weights.")


def limitations() -> str:
    out = []
    has_h = bool(human.load_labels(config.DATA / "human_labels.jsonl", config.DATA / "human_sample_key.jsonl"))
    agg = teacher_pass_rates()
    lenient = f" On grounding it is lenient (it passes {lenient_sentence(agg)})." if any(
        k == "grounding" and v != "gold" for k, v in agg) else ""
    out.append("- **Lorde imitates an 8B teacher and inherits its blind spots.**" + lenient
               + ("" if has_h else " Human agreement, the only check on the teacher itself, has not been measured "
                  "(0 hand labels)")
               + ("" if (config.DATA / "labels_32b.jsonl").exists() else
                  (", and" if not has_h else " The") + " the planned 32B calibration slice was not run")
               + ". High agreement with the teacher is not evidence that the decisions are right.")
    b = bench(CAND)
    if b.get("vs_teacher_8b"):
        g = b["vs_teacher_8b"]
        full = (judge_eval().get("judges", {}).get(CAND) or {}).get("vs_teacher") or {}
        out.append(f"- **The headline is the easy number.** On the balanced golden-300 subset Lorde agrees with the "
                   f"teacher on {pct(g.get('decision_agreement'))} ({kappa_eq(g.get('kappa'))}), against "
                   f"{pct(full.get('decision_agreement'))} ({kappa_eq(full.get('kappa'))}) on the full test set.")
    if b.get("adversarial_pass_rate") is not None:
        so = bench(f"{CAND}+so").get("adversarial_pass_rate")
        by = sorted((b.get("adversarial_by_attack") or {}).items(), key=lambda kv: -kv[1])[:2]
        out.append(f"- **Judged content can instruct the judge, and Lorde does not resist it:** on adversarial-100 it "
                   f"passes {b['adversarial_pass_rate']:.0%} of items whose reference says *fail*"
                   + (f" ({so:.0%} with structured outputs)" if so is not None else "") + "; target ≤ 5%"
                   + (f", weakest on {' and '.join(f'{k.replace(chr(95), chr(32))} ({v:.0%})' for k, v in by)}" if by
                      else "")
                   + ". High-stakes datasets still need a human in the loop.")
    short = short_missed()
    if short:
        out.append("- **It misses its own acceptance criteria and is not promotable** (full list under *Results*): "
                   + "; ".join(short) + ".")
    nd = leakage_stats()
    if nd and (nd.get("test_prompts_near_train") or nd.get("test_gold_responses_identical_to_train")):
        out.append(f"- **Near-duplicate leakage:** {nd['test_prompts_near_train']} of {nd['test_prompts']} test "
                   f"reward prompts have a ≥ {nd.get('threshold', 0.9):.0%}-similar training prompt and "
                   f"{nd['test_gold_responses_identical_to_train']} test gold responses equal a training one.")
    dn = downstream()
    if dn.get("metrics"):
        out.append("- **The downstream study is small** (one seed, one training run per set, capped rows), "
                   "groundedness regresses significantly, and the teacher-gated baseline is depressed by the "
                   "teacher's parse errors (see *Reading the downstream result*).")
    out.append("- It is trained on CuratorKIT's default RewardGate and HallucinationGate templates. Other rubrics are "
               "out of distribution; template fingerprints flag them.")
    c = cache_note()
    if c:
        out.append("- **Prediction provenance:** " + c + " Re-running step 6 without the flag recomputes them.")
    return "\n".join(out)


def reproduce_note() -> str:
    c = cache_note()
    return ("To recompute the step-6 predictions instead of reusing the cached ones, run step 6 (the driver's "
            "Session D `judge-timing` step) without `LORDE_TRUST_LEGACY_CACHE=1`; see the runbook.") if c else ""


def gpu_class(name: str | None) -> str | None:
    """'t4' for "Tesla T4", 'l4' for "NVIDIA L4", None for anything else."""
    tokens = re.split(r"[^A-Z0-9]+", (name or "").upper())
    return next((g for g in config.GPU_HOURLY if g.upper() in tokens), None)


def roi(rows: int, gpu: str | None) -> str:
    r = judge_eval()
    if not r:
        return "_Pending: run forge/06_judge_eval.py._"
    lorde = r["judges"].get(CAND) or {}
    perf, api = lorde.get("perf", {}), r.get("api_reference")
    jps = perf.get("judgments_per_s")
    if not (jps and api):
        return "_Pending: needs measured throughput and API token counts._"
    hw = (r.get("hardware") or {}).get("name")
    measured = gpu_class(hw)
    if gpu and measured and gpu != measured:
        return (f"_Not shown: throughput was measured on {hw}; a {gpu.upper()} price would describe hardware "
                "that was not measured. Re-run with --gpu " + measured + "._")
    price = lorde.get("gpu_hourly") or (config.GPU_HOURLY.get(measured or gpu or "") if (measured or gpu) else None)
    if not price:
        return "_Pending: no GPU price for the measured hardware (pass --gpu-hourly to step 6)._"
    where = f"1× {hw}" if hw else "the evaluation GPU (not recorded by this run)"
    judgments = rows * 2  # both gates judge every row
    hours = judgments / jps / 3600
    self_cost = hours * price
    api_cost = judgments * api["cost_per_1M_judgments"] / 1e6
    smp = perf.get("sample")
    how = ("stratified random sample, timed before any prediction" if smp else
           "first test items after the predictions (warm cache, mostly short reward items: optimistic)")
    measured_tokens = api.get("token_source") == "calibration labels"
    tok_note = "32B calibration replies" if measured_tokens else "the 8B teacher's token counts; the API was not called"
    out = [f"Scenario: curating a **{rows:,}-row** dataset through both gates = **{judgments:,} judgments**.\n",
           table(["", "Aya Expanse 32B via Cohere API (list-price estimate)", f"Lorde on {where} (measured)"],
                 [["Price", f"${api['price_per_1M_tokens']['input']}/1M in, ${api['price_per_1M_tokens']['output']}/1M "
                   "out (list price)", f"${price}/GPU-hour"],
                  ["Tokens per judgment", f"{api['mean_prompt_tokens']} in + {api['mean_completion_tokens']} out "
                                          f"({tok_note})",
                   f"{perf.get('mean_prompt_tokens')} in + {perf.get('mean_completion_tokens')} out"],
                  ["Throughput", "–", f"{jps} judgments/s, {perf.get('completion_tokens_per_s')} output tokens/s "
                                      f"(measured, {perf.get('concurrency')} concurrent; {how})"],
                  ["GPU runtime", "–", f"{hours:.2f} h"],
                  ["Cost for this dataset", f"${api_cost:,.2f} (estimate)", f"${self_cost:,.2f}"],
                  ["Cost per 1M judgments", f"${api['cost_per_1M_judgments']:,.2f} (estimate)",
                   f"${price / (jps * 3600) * 1e6:,.2f}"]]), "",
           f"**{api_cost / self_cost:.0f}× cheaper** at equal volume, on the hardware the throughput was measured on "
           f"({where}, ${price}/h). The API side is list price times token counts, not a measured bill. Other GPUs "
           "will differ; we did not measure them. Cheaper is only useful at the agreement levels above."]
    return "\n".join(out)


def tldr() -> str:
    r = judge_eval()
    j = (r.get("judges", {}).get(CAND) or {}).get("vs_teacher")
    b = (r.get("judges", {}).get("base") or {}).get("vs_teacher")
    if not j:
        return ""
    n = r.get("n_test")
    s = (f"Lorde's replies are **{pct(j['gate_usable_rate'])}** gate-usable"
         + (f" (base Tiny Aya Global: {pct(b['gate_usable_rate'])})" if b else "")
         + f". On the {f'{n:,}-item ' if n else ''}held-out test set it agrees with the teacher on **{pct(j['decision_agreement'])}** "
         f"of pass/reject decisions ({kappa_eq(j.get('kappa'))}"
         + (f"; base: {pct(b['decision_agreement'])}" if b else "") + ")")
    g = bench(CAND).get("vs_teacher_8b")
    if g:
        s += (f", and on the balanced golden-300 subset on **{pct(g['decision_agreement'])}** "
              f"({kappa_eq(g.get('kappa'))})")
    c = (r["judges"][CAND]).get("cost_per_1M")
    a = (r.get("api_reference") or {}).get("cost_per_1M_judgments")
    if c and a:
        hw = (r.get("hardware") or {}).get("name")
        s += (f". It costs **${c:.2f} per 1M judgments**{f' on a {hw}' if hw else ''} vs ${a:.2f} estimated at list "
              f"price for Aya Expanse 32B through the Cohere API ({a / c:.0f}×)")
    s += "."
    agg = teacher_pass_rates()
    caveats = []
    if any(k == "grounding" and v != "gold" for k, v in agg):
        caveats.append(f"the teacher is a lenient grounding reference (it passes {lenient_sentence(agg)})")
    has_h = bool(human.load_labels(config.DATA / "human_labels.jsonl", config.DATA / "human_sample_key.jsonl"))
    if not has_h:
        caveats.append("no human labels have been collected yet")
    if not (config.DATA / "labels_32b.jsonl").exists():
        caveats.append("the planned 32B calibration was not run")
    if caveats:
        s += (" **That agreement measures imitation, not correctness:** " + "; ".join(caveats) + ".")
    if r.get("promotable") is False:
        s += " By its own acceptance criteria Lorde is **not promotable** yet (see *Results*)."
    d = downstream()
    m = d.get("metrics") or {}
    if CAND in m and "teacher" in m:
        f1 = {k: v.get("f1_score") for k, v in m.items()}
        s += (f" A QA model trained on Lorde-gated data reaches F1 {f1[CAND]:.3f} (teacher-gated {f1['teacher']:.3f}"
              + (f", ungated {f1['ungated']:.3f}" if f1.get("ungated") is not None else "")
              + (f", untrained base {f1['base']:.3f}" if f1.get("base") is not None else "") + ")")
        sig = d.get("significance") or {}
        worse = [(k.split(" vs ")[1], v["lexical_groundedness"]) for k, v in sig.items()
                 if (v.get("lexical_groundedness") or {}).get("significant")
                 and (v["lexical_groundedness"].get("delta") or 0) < 0]
        if worse:
            s += (", but its answers are significantly **less grounded** than "
                  + " and ".join(f"{set_name(b_)} ({w['mean_candidate']:.3f} vs {w['mean_baseline']:.3f}, "
                                 f"{pval(w.get('p_value'))})" for b_, w in worse))
        s += "; one seed per set, so this is not yet evidence of parity."
    return s


def build(rows: int = 100_000, gpu: str | None = None) -> str:
    """The article as markdown, from the template and the result files under config.ROOT."""
    t = TEMPLATE.read_text()
    fills = {
        "<!-- tldr -->": tldr(),
        "<!-- roi -->": roi(rows, gpu),
        "<!-- acceptance -->": acceptance_md(),
        "<!-- labels_note -->": labels_note(),
        "<!-- human_status -->": human_status(),
        "<!-- leakage -->": leakage(),
        "<!-- train_recipe -->": train_recipe(),
        "<!-- teacher_audit -->": teacher_audit(),
        "<!-- bench -->": bench_md(),
        "<!-- downstream_notes -->": downstream_notes(),
        "<!-- limitations -->": limitations(),
        "<!-- reproduce_note -->": reproduce_note(),
    }
    for name in STEP:
        fills[f"<!-- results:{name} -->"] = section(name)
    for k, v in fills.items():
        t = t.replace(k, v)
    return re.sub(r"\n{3,}", "\n\n", t)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=100_000)
    ap.add_argument("--gpu", choices=list(config.GPU_HOURLY), default=None,
                    help="price class when judge_eval.json has no gpu_hourly; must match the measured GPU")
    ap.add_argument("--out", default=str(DOCS / "HF_ARTICLE.md"))
    args = ap.parse_args()
    t = build(args.rows, args.gpu)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(t, encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
