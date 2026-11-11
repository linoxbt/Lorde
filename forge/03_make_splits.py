#!/usr/bin/env python
"""Step 3: grouped splits and canonical SFT targets.

    python forge/03_make_splits.py --human-langs en,fr,es

Outputs
  data/sft/train.jsonl, data/sft/val.jsonl   SFT rows: messages + prompt/completion
  data/test.jsonl                            held-out items with teacher labels
  data/human_sample.jsonl                    200 test items to hand-label (step 4), blind: opaque ids,
                                             no variant/group, shuffled
  data/human_sample_key.jsonl                private key: opaque id -> uid, variant, group (never shown to
                                             labellers; step 6 uses it to join the labels)

Splits are grouped by prompt (reward) or source chunk (grounding), so no test
prompt or source passage appears in training. Only gate-usable teacher replies
become targets; they are re-serialized to the gate's exact keys (canonical JSON).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import canonical, config, contract, human, jsonl, splits  # noqa: E402

ITEM_FIELDS = ("instruction", "response", "source_text", "question", "answer")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default=str(config.DATA / "pool.jsonl"))
    ap.add_argument("--labels", default=str(config.DATA / "labels.jsonl"))
    ap.add_argument("--val", type=float, default=0.05)
    ap.add_argument("--test", type=float, default=0.15)
    ap.add_argument("--human-langs", default="en", help="comma-separated languages you can read")
    ap.add_argument("--human-n", type=int, default=200)
    ap.add_argument("--blind-salt", default=None,
                    help="salt for the opaque human-sample ids (default: random; ids already in the key are kept)")
    ap.add_argument("--max-prompt-chars", type=int, default=7000,
                    help="rough guard; step 5 drops rows over 2,048 tokens with the real tokenizer")
    args = ap.parse_args()

    pool = {r["uid"]: r for r in jsonl.read(args.pool)}
    labels = {r["uid"]: r for r in jsonl.read(args.labels) if r["uid"] in pool and not r.get("call_error")}
    if not labels:
        sys.exit("no labels: run forge/02_teacher_label.py first")

    rows, dropped = [], Counter()
    for uid, lab in labels.items():
        item = pool[uid]
        j = contract.parse_reply(item["kind"], lab["raw"])
        target = canonical.canonical(j)
        if target is None:
            dropped["teacher_unparseable"] += 1
            continue
        prompt = contract.render(item)
        if len(prompt) > args.max_prompt_chars:
            dropped["too_long"] += 1
            continue
        rows.append({**item, "prompt_text": prompt, "target": target, "teacher_score": j.score,
                     "teacher_raw": lab["raw"], "teacher_verdict": j.verdict})

    parts = splits.grouped_split(rows, val=args.val, test=args.test)

    def sft_row(r: dict) -> dict:
        user = {"role": "user", "content": r["prompt_text"]}
        asst = {"role": "assistant", "content": canonical.dumps(r["target"])}
        return {"uid": r["uid"], "kind": r["kind"], "lang": r["lang"], "messages": [user, asst],
                "prompt": [user], "completion": [asst]}

    def test_row(r: dict) -> dict:
        out = {k: r[k] for k in ("uid", "kind", "lang", "variant", "group") if k in r}
        out.update({k: r[k] for k in ITEM_FIELDS if k in r})
        out.update({"prompt": r["prompt_text"], "teacher_score": r["teacher_score"],
                    "teacher_raw": r["teacher_raw"], "teacher_verdict": r["teacher_verdict"],
                    "teacher_decision": r["teacher_score"] >= config.THRESHOLD})
        return out

    sft = config.DATA / "sft"
    n_train = jsonl.write(sft / "train.jsonl", (sft_row(r) for r in parts["train"]))
    n_val = jsonl.write(sft / "val.jsonl", (sft_row(r) for r in parts["val"]))
    test = [test_row(r) for r in parts["test"]]
    n_test = jsonl.write(config.DATA / "test.jsonl", test)

    langs = [x.strip() for x in args.human_langs.split(",") if x.strip()]
    human_pool = [t for t in test if t["lang"] in langs]
    sample = splits.stratified_sample(human_pool, args.human_n, keys=("kind", "lang", "variant"), salt="human")
    human.write_blind(sample, config.DATA / "human_sample.jsonl", config.DATA / "human_sample_key.jsonl",
                      salt=args.blind_salt)

    stats = {
        "labels": len(labels), "usable": len(rows), "dropped": dict(dropped),
        "train": n_train, "val": n_val, "test": n_test, "human_sample": len(sample),
        "test_by_kind_lang": Counter(f"{t['kind']}:{t['lang']}" for t in test),
        "test_teacher_pass_rate": round(sum(t["teacher_decision"] for t in test) / max(1, n_test), 4),
        "train_target_score_hist": Counter(round(r["teacher_score"], 1) for r in parts["train"]),
    }
    jsonl.write_json(config.DATA / "splits_summary.json", stats)
    print(f"[splits] train={n_train} val={n_val} test={n_test} human_sample={len(sample)} dropped={dict(dropped)}")
    if len(sample) < args.human_n:
        print(f"[splits] note: only {len(sample)} human-sample items available in langs {langs}")


if __name__ == "__main__":
    main()
