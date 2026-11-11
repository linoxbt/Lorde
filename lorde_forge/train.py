"""LoRA SFT shared by step 5 (the judge) and step 8 (downstream models).

AlignTune is the primary engine (TRL backend); plain TRL is the fallback. Both
train on prompt/completion rows, so the loss covers the completion only, save a
checkpoint every ``save_steps`` (the two newest are kept) and resume from the
newest complete one when it exists, so a killed session loses little. Both
report the validation loss when a validation file is given.
Heavy imports are lazy: this module imports cleanly without torch.
"""

from __future__ import annotations

import math
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


@dataclass
class Recipe:
    epochs: int = 2
    batch_size: int = 1
    grad_accum: int = 16
    lr: float = 1e-4
    warmup_frac: float = 0.03
    max_len: int = 1536
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    targets: list[str] = field(default_factory=lambda: list(LORA_TARGETS))
    save_steps: int = 200
    eval_steps: int = 200

    def warmup_steps(self, n_rows: int) -> int:
        steps = math.ceil(n_rows / (self.batch_size * self.grad_accum)) * self.epochs
        return max(1, int(self.warmup_frac * steps))


def precision() -> tuple[str, bool, bool]:
    """('bf16'|'fp16'|'fp32', fp16, bf16): bf16 on L4/A100, fp16 on T4 (no bf16), fp32 without a GPU."""
    import torch

    if not torch.cuda.is_available():
        return "fp32", False, False
    # Native bf16 needs compute capability 8.0+ (Ampere). is_bf16_supported() also counts software emulation,
    # so it is True on a T4 (7.5), where bf16 then runs ~10x slower than fp16 on the tensor cores.
    if torch.cuda.get_device_capability()[0] >= 8:
        return "bf16", False, True
    return "fp16", True, False


def device_name() -> str | None:
    import torch

    return torch.cuda.get_device_name(0) if torch.cuda.is_available() else None


def torch_dtype():
    import torch

    prec, _, _ = precision()
    return {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[prec]


def token_len(tok, row: dict) -> int:
    p, c = row["prompt"], row["completion"]
    if isinstance(p, list):  # conversational
        ids = tok.apply_chat_template(p + c, tokenize=True)
        # transformers 5 returns a BatchEncoding (a Mapping, not a dict); len() of it counts its keys.
        return len(ids["input_ids"] if hasattr(ids, "keys") else ids)
    return len(tok(p + c)["input_ids"])


def filter_by_tokens(rows: list[dict], tok, max_len: int) -> tuple[list[dict], int]:
    keep = [r for r in rows if token_len(tok, r) <= max_len]
    return keep, len(rows) - len(keep)


def render_chat(rows: list[dict], tok) -> list[dict]:
    """Conversational prompt/completion rows -> plain strings with the chat template applied.

    AlignTune flattens chat-format columns to plain text before TRL sees them, which would train
    without the chat template that vLLM applies at serving time. Rendering here (prompt with the
    generation prompt, completion = the rest of the full conversation, leading BOS removed because
    the tokenizer adds it) gives token ids identical to TRL's own conversational path.
    """
    adds_bos = tok.bos_token is not None and tok("x")["input_ids"][:1] == [tok.bos_token_id]
    out = []
    for r in rows:
        p, c = r["prompt"], r["completion"]
        if not isinstance(p, list):
            out.append({"prompt": p, "completion": c})
            continue
        full = tok.apply_chat_template(p + c, tokenize=False)
        prompt = tok.apply_chat_template(p, tokenize=False, add_generation_prompt=True)
        if not full.startswith(prompt):
            raise ValueError("chat template: the prompt is not a prefix of the full conversation")
        completion = full[len(prompt):]
        if adds_bos and prompt.startswith(tok.bos_token):
            prompt = prompt[len(tok.bos_token):]
        out.append({"prompt": prompt, "completion": completion})
    return out


def latest_checkpoint(out_dir: Path) -> str | None:
    """Newest complete checkpoint-N under ``out_dir`` (trainer_state.json is written last), or None."""
    ckpts = [c for c in Path(out_dir).glob("checkpoint-*")
             if c.name.split("-")[-1].isdigit() and (c / "trainer_state.json").exists()]
    return str(max(ckpts, key=lambda c: int(c.name.split("-")[-1]))) if ckpts else None


@contextmanager
def resume_trl_from(checkpoint: str | None):
    """AlignTune 0.1.32 calls ``trl.SFTTrainer.train()`` without arguments and has no resume option; while
    this is active, that call resumes from ``checkpoint`` (an explicit argument still wins)."""
    if not checkpoint:
        yield
        return
    import trl

    cls = trl.SFTTrainer
    original = cls.train

    def train(self, resume_from_checkpoint=None, *args, **kwargs):
        return original(self, resume_from_checkpoint or checkpoint, *args, **kwargs)

    cls.train = train
    try:
        yield
    finally:
        cls.train = original


def attach_validation(trainer, train_path: Path, val_path: Path) -> bool:
    """Give an AlignTune trainer our validation split (processed exactly like the training rows) as its eval
    set. AlignTune only splits off random validation rows itself; ours is grouped by prompt like the test set."""
    ds_cfg = trainer.config.dataset
    try:
        if getattr(trainer, "model", None) is None:
            trainer.setup_model()
        ds_cfg.name = str(val_path)
        trainer.setup_data()
        val = trainer.train_dataset
        ds_cfg.name = str(train_path)
        trainer.setup_data()
        trainer.dataset, trainer.eval_dataset = trainer.train_dataset, val
        print(f"[train] validation loss on {len(val)} rows every eval step")
        return True
    except Exception as e:  # noqa: BLE001  (AlignTune internals: never lose the training run over eval)
        print(f"[train] no validation loss with AlignTune ({type(e).__name__}: {e}); training without it")
        ds_cfg.name = str(train_path)
        trainer.dataset = trainer.eval_dataset = None
        return False


def train_aligntune(base_model: str, train_path: Path, out_dir: Path, n_rows: int, r: Recipe,
                    val_path: Path | None = None) -> None:
    from aligntune.core.backend_factory import create_sft_trainer
    from transformers import AutoTokenizer

    from . import jsonl

    tok = AutoTokenizer.from_pretrained(base_model)
    rendered = train_path.with_name(train_path.stem + ".rendered.jsonl")
    jsonl.write(rendered, render_chat(jsonl.read(train_path), tok))
    train_path = rendered
    if val_path is not None and jsonl.read(val_path):
        val_rendered = val_path.with_name(val_path.stem + ".rendered.jsonl")
        jsonl.write(val_rendered, render_chat(jsonl.read(val_path), tok))
        val_path = val_rendered
    else:
        val_path = None
    prec, fp16, bf16 = precision()
    trainer = create_sft_trainer(
        model_name=base_model, dataset_name=str(train_path), backend="trl", output_dir=str(out_dir),
        task_type="instruction_following", num_epochs=r.epochs, batch_size=r.batch_size,
        gradient_accumulation_steps=r.grad_accum, learning_rate=r.lr, lr_scheduler_type="cosine",
        warmup_steps=r.warmup_steps(n_rows), max_seq_length=r.max_len, use_peft=True, lora_r=r.lora_r,
        lora_alpha=r.lora_alpha, lora_dropout=r.lora_dropout, lora_target_modules=r.targets,
        precision=prec, fp16=fp16, bf16=bf16, gradient_checkpointing=True, completion_only_loss=True,
        packing=False, eval_strategy="steps" if val_path else "no", eval_interval=r.eval_steps,
        load_best_model_at_end=False, save_interval=r.save_steps, save_total_limit=2,
        logging_steps=10, seed=42,
    )
    if val_path is not None:
        attach_validation(trainer, train_path, val_path)
    last = latest_checkpoint(out_dir)
    if last:
        print(f"[train] resuming from {last}")
    with resume_trl_from(last):
        trainer.train()
    trainer.save_model(str(out_dir))


def train_trl(base_model: str, train_path: Path, val_path: Path | None, out_dir: Path, n_rows: int,
              r: Recipe) -> None:
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
        logging_steps=10, eval_strategy="steps" if val_path else "no", eval_steps=r.eval_steps,
        save_strategy="steps", save_steps=r.save_steps, save_total_limit=2, report_to="none", seed=42,
        model_init_kwargs={"dtype": torch_dtype()},
    )
    peft_cfg = LoraConfig(r=r.lora_r, lora_alpha=r.lora_alpha, lora_dropout=r.lora_dropout,
                          target_modules=r.targets, task_type="CAUSAL_LM")
    trainer = SFTTrainer(model=base_model, args=cfg, train_dataset=ds["train"],
                         eval_dataset=ds.get("validation"), peft_config=peft_cfg)
    last = latest_checkpoint(out_dir)
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
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    dtype = torch_dtype()
    src = find_adapter(adapter_dir)
    print(f"[train] merging {src} into {base_model} ({dtype})")
    base = AutoModelForCausalLM.from_pretrained(base_model, dtype=dtype, device_map="cpu")
    model = PeftModel.from_pretrained(base, str(src)).merge_and_unload()
    merged_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(merged_dir), safe_serialization=True)
    AutoTokenizer.from_pretrained(base_model).save_pretrained(str(merged_dir))
