#!/usr/bin/env python
"""Step 5: LoRA SFT of Tiny Aya Global on canonical teacher replies, then merge.

    python forge/05_train_sft.py                 # AlignTune (TRL backend)
    python forge/05_train_sft.py --engine trl    # plain TRL fallback
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
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, jsonl  # noqa: E402

LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def filter_by_tokens(rows: list[dict], tok, max_len: int) -> tuple[list[dict], int]:
    keep, dropped = [], 0
    for r in rows:
        ids = tok.apply_chat_template(r["prompt"] + r["completion"], tokenize=True)
        n = len(ids["input_ids"] if isinstance(ids, dict) else ids)
        if n <= max_len:
            keep.append(r)
        else:
            dropped += 1
    return keep, dropped


def precision() -> tuple[str, bool, bool]:
    import torch

    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        return "bf16", False, True
    return "fp16", True, False


def train_aligntune(args, train_path: Path, val_path: Path, adapter_dir: Path, n_train: int) -> None:
    from aligntune.core.backend_factory import create_sft_trainer

    prec, fp16, bf16 = precision()
    steps = math.ceil(n_train / (args.batch_size * args.grad_accum)) * args.epochs
    trainer = create_sft_trainer(
        model_name=args.base_model,
        dataset_name=str(train_path),
        backend="trl",
        output_dir=str(adapter_dir),
        task_type="instruction_following",
        num_epochs=args.epochs,
        batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=max(1, int(0.03 * steps)),
        max_seq_length=config.MAX_SEQ_LEN,
        use_peft=True,
        lora_r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        lora_target_modules=LORA_TARGETS,
        precision=prec,
        fp16=fp16,
        bf16=bf16,
        gradient_checkpointing=True,
        completion_only_loss=True,
        packing=False,
        # AlignTune takes one dataset path; validation loss is tracked by the TRL engine
        # and the held-out test set is scored in step 6.
        eval_strategy="no",
        load_best_model_at_end=False,
        save_interval=200,
        logging_steps=10,
        seed=42,
    )
    trainer.train()
    trainer.save_model(str(adapter_dir))


def train_trl(args, train_path: Path, val_path: Path, adapter_dir: Path, n_train: int) -> None:
    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer

    prec, fp16, bf16 = precision()
    ds = load_dataset("json", data_files={"train": str(train_path), "validation": str(val_path)})
    ds = ds.select_columns(["prompt", "completion"])
    steps = math.ceil(n_train / (args.batch_size * args.grad_accum)) * args.epochs
    cfg = SFTConfig(
        output_dir=str(adapter_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=max(1, int(0.03 * steps)),
        max_length=config.MAX_SEQ_LEN,
        completion_only_loss=True,
        packing=False,
        fp16=fp16,
        bf16=bf16,
        gradient_checkpointing=True,
        logging_steps=10,
        eval_strategy="steps",
        eval_steps=100,
        save_strategy="steps",
        save_steps=200,
        save_total_limit=2,
        report_to="none",
        seed=42,
        model_init_kwargs={"dtype": torch.bfloat16 if bf16 else torch.float16},
    )
    peft_cfg = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, target_modules=LORA_TARGETS,
                          task_type="CAUSAL_LM")
    trainer = SFTTrainer(model=args.base_model, args=cfg, train_dataset=ds["train"],
                         eval_dataset=ds["validation"], peft_config=peft_cfg)
    last = None
    ckpts = sorted(adapter_dir.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1]))
    if ckpts:
        last = str(ckpts[-1])
        print(f"[train] resuming from {last}")
    trainer.train(resume_from_checkpoint=last)
    trainer.save_model(str(adapter_dir))


def find_adapter(adapter_dir: Path) -> Path:
    if (adapter_dir / "adapter_config.json").exists():
        return adapter_dir
    hits = sorted(adapter_dir.rglob("adapter_config.json"), key=lambda p: p.stat().st_mtime)
    if not hits:
        sys.exit(f"no LoRA adapter found under {adapter_dir}")
    return hits[-1].parent


def merge(base_model: str, adapter_dir: Path, merged_dir: Path) -> None:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    _, _, bf16 = precision()
    dtype = torch.bfloat16 if bf16 else torch.float16
    src = find_adapter(adapter_dir)
    print(f"[train] merging {src} into {base_model} ({dtype})")
    base = AutoModelForCausalLM.from_pretrained(base_model, dtype=dtype, device_map="cpu")
    model = PeftModel.from_pretrained(base, str(src)).merge_and_unload()
    merged_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(merged_dir), safe_serialization=True)
    AutoTokenizer.from_pretrained(base_model).save_pretrained(str(merged_dir))


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

    run = config.RUNS / args.run
    adapter_dir, merged_dir = run / "adapter", run / "merged"
    run.mkdir(parents=True, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(args.base_model)

    t0 = time.time()
    meta: dict = {"base_model": args.base_model, "engine": args.engine, "epochs": args.epochs,
                  "lr": args.lr, "effective_batch": args.batch_size * args.grad_accum,
                  "lora": {"r": 16, "alpha": 32, "dropout": 0.05, "targets": LORA_TARGETS}}
    if not args.skip_train:
        paths = {}
        for name, src in (("train", args.train), ("val", args.val)):
            rows = jsonl.read(src)
            if args.max_samples:
                rows = rows[: args.max_samples if name == "train" else max(8, args.max_samples // 10)]
            keep, dropped = filter_by_tokens(rows, tok, config.MAX_SEQ_LEN)
            dst = run / f"{name}.prompt_completion.jsonl"
            jsonl.write(dst, ({"prompt": r["prompt"], "completion": r["completion"]} for r in keep))
            paths[name] = dst
            meta[f"{name}_rows"], meta[f"{name}_dropped_over_{config.MAX_SEQ_LEN}"] = len(keep), dropped
            print(f"[train] {name}: {len(keep)} rows ({dropped} dropped for length)")
        fn = train_aligntune if args.engine == "aligntune" else train_trl
        fn(args, paths["train"], paths["val"], adapter_dir, meta["train_rows"])
        meta["train_seconds"] = round(time.time() - t0, 1)

    merge(args.base_model, adapter_dir, merged_dir)
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
