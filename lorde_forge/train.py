"""LoRA SFT shared by step 5 (the judge) and step 8 (downstream models).

AlignTune is the primary engine (TRL backend); plain TRL is the fallback. Both
train on prompt/completion rows, so the loss covers the completion only.
Heavy imports are lazy: this module imports cleanly without torch.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


@dataclass
class Recipe:
    epochs: int = 2
    batch_size: int = 2
    grad_accum: int = 8
    lr: float = 1e-4
    warmup_frac: float = 0.03
    max_len: int = 2048
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    targets: list[str] = field(default_factory=lambda: list(LORA_TARGETS))

    def warmup_steps(self, n_rows: int) -> int:
        steps = math.ceil(n_rows / (self.batch_size * self.grad_accum)) * self.epochs
        return max(1, int(self.warmup_frac * steps))


def precision() -> tuple[str, bool, bool]:
    """('bf16'|'fp16', fp16, bf16): T4 has no bf16."""
    import torch

    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        return "bf16", False, True
    return "fp16", True, False


def token_len(tok, row: dict) -> int:
    p, c = row["prompt"], row["completion"]
    if isinstance(p, list):  # conversational
        ids = tok.apply_chat_template(p + c, tokenize=True)
        return len(ids["input_ids"] if isinstance(ids, dict) else ids)
    return len(tok(p + c)["input_ids"])


def filter_by_tokens(rows: list[dict], tok, max_len: int) -> tuple[list[dict], int]:
    keep = [r for r in rows if token_len(tok, r) <= max_len]
    return keep, len(rows) - len(keep)


def train_aligntune(base_model: str, train_path: Path, out_dir: Path, n_rows: int, r: Recipe) -> None:
    from aligntune.core.backend_factory import create_sft_trainer

    prec, fp16, bf16 = precision()
    trainer = create_sft_trainer(
        model_name=base_model, dataset_name=str(train_path), backend="trl", output_dir=str(out_dir),
        task_type="instruction_following", num_epochs=r.epochs, batch_size=r.batch_size,
        gradient_accumulation_steps=r.grad_accum, learning_rate=r.lr, lr_scheduler_type="cosine",
        warmup_steps=r.warmup_steps(n_rows), max_seq_length=r.max_len, use_peft=True, lora_r=r.lora_r,
        lora_alpha=r.lora_alpha, lora_dropout=r.lora_dropout, lora_target_modules=r.targets,
        precision=prec, fp16=fp16, bf16=bf16, gradient_checkpointing=True, completion_only_loss=True,
        packing=False, eval_strategy="no", load_best_model_at_end=False, save_interval=200,
        logging_steps=10, seed=42,
    )
    trainer.train()
    trainer.save_model(str(out_dir))


def train_trl(base_model: str, train_path: Path, val_path: Path | None, out_dir: Path, n_rows: int,
              r: Recipe) -> None:
    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from trl import SFTConfig, SFTTrainer

    _, fp16, bf16 = precision()
    files = {"train": str(train_path)}
    if val_path:
        files["validation"] = str(val_path)
    ds = load_dataset("json", data_files=files).select_columns(["prompt", "completion"])
    cfg = SFTConfig(
        output_dir=str(out_dir), num_train_epochs=r.epochs, per_device_train_batch_size=r.batch_size,
        per_device_eval_batch_size=r.batch_size, gradient_accumulation_steps=r.grad_accum, learning_rate=r.lr,
        lr_scheduler_type="cosine", warmup_steps=r.warmup_steps(n_rows), max_length=r.max_len,
        completion_only_loss=True, packing=False, fp16=fp16, bf16=bf16, gradient_checkpointing=True,
        logging_steps=10, eval_strategy="steps" if val_path else "no", eval_steps=100, save_strategy="steps",
        save_steps=200, save_total_limit=2, report_to="none", seed=42,
        model_init_kwargs={"dtype": torch.bfloat16 if bf16 else torch.float16},
    )
    peft_cfg = LoraConfig(r=r.lora_r, lora_alpha=r.lora_alpha, lora_dropout=r.lora_dropout,
                          target_modules=r.targets, task_type="CAUSAL_LM")
    trainer = SFTTrainer(model=base_model, args=cfg, train_dataset=ds["train"],
                         eval_dataset=ds.get("validation"), peft_config=peft_cfg)
    ckpts = sorted(out_dir.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1]))
    last = str(ckpts[-1]) if ckpts else None
    if last:
        print(f"[train] resuming from {last}")
    trainer.train(resume_from_checkpoint=last)
    trainer.save_model(str(out_dir))


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
