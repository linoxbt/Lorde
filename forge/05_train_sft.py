#!/usr/bin/env python
"""Step 5: LoRA SFT of Tiny Aya Global on canonical teacher replies, then merge.

    python forge/05_train_sft.py                 # AlignTune (TRL backend)
    python forge/05_train_sft.py --engine trl    # plain TRL fallback
    python forge/05_train_sft.py --max-samples 64 --epochs 1   # smoke test first
    python forge/05_train_sft.py --push <hf-user>/lorde-tiny-aya   # optional, private

Recipe (spec Section 6): LoRA r=16, alpha=32, dropout=0.05 on all attention and
MLP projections; lr 1e-4, cosine, 3% warmup, 2 epochs, effective batch 16
(2 x 8 accumulation); loss on the assistant reply only; rows longer than 2,048
tokens are dropped, never truncated. fp16 on T4, bf16 on L4/A100.

Outputs runs/lorde-v1/{adapter,merged,train_meta.json}. Runs in the forge-train env.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, jsonl  # noqa: E402
from lorde_forge import train as T  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["aligntune", "trl"], default="aligntune")
    ap.add_argument("--base-model", default=config.STUDENT_MODEL)
    ap.add_argument("--train", default=str(config.DATA / "sft" / "train.jsonl"))
    ap.add_argument("--val", default=str(config.DATA / "sft" / "val.jsonl"))
    ap.add_argument("--run", default=config.RUN_NAME)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--max-samples", type=int, default=0, help="smoke test on N rows")
    ap.add_argument("--skip-train", action="store_true", help="only merge an existing adapter")
    ap.add_argument("--push", default=None, help="HF repo id to push merged weights to (private)")
    args = ap.parse_args()

    from transformers import AutoTokenizer

    recipe = T.Recipe(epochs=args.epochs, batch_size=args.batch_size, grad_accum=args.grad_accum, lr=args.lr,
                      max_len=config.MAX_SEQ_LEN)
    run = config.RUNS / args.run
    adapter_dir, merged_dir = run / "adapter", run / "merged"
    run.mkdir(parents=True, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(args.base_model)
    meta: dict = {"base_model": args.base_model, "engine": args.engine, "recipe": asdict(recipe)}

    t0 = time.time()
    if not args.skip_train:
        paths = {}
        for name, src in (("train", args.train), ("val", args.val)):
            rows = jsonl.read(src)
            if args.max_samples:
                rows = rows[: args.max_samples if name == "train" else max(8, args.max_samples // 10)]
            keep, dropped = T.filter_by_tokens(rows, tok, recipe.max_len)
            dst = run / f"{name}.prompt_completion.jsonl"
            jsonl.write(dst, ({"prompt": r["prompt"], "completion": r["completion"]} for r in keep))
            paths[name] = dst
            meta[f"{name}_rows"], meta[f"{name}_dropped_over_max_len"] = len(keep), dropped
            print(f"[train] {name}: {len(keep)} rows ({dropped} dropped for length)")
        if args.engine == "aligntune":
            T.train_aligntune(args.base_model, paths["train"], adapter_dir, meta["train_rows"], recipe)
        else:
            T.train_trl(args.base_model, paths["train"], paths["val"], adapter_dir, meta["train_rows"], recipe)
        meta["train_seconds"] = round(time.time() - t0, 1)

    T.merge(args.base_model, adapter_dir, merged_dir)
    (run / "train_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"[train] merged model -> {merged_dir}")

    if args.push:
        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(args.push, private=True, exist_ok=True)
        api.upload_folder(repo_id=args.push, folder_path=str(merged_dir), commit_message=f"{args.run} merged")
        print(f"[train] pushed to https://huggingface.co/{args.push} (private)")


if __name__ == "__main__":
    main()
