"""render_chat: AlignTune flattens chat rows, so we apply the chat template ourselves (no torch needed)."""

from lorde_forge.train import Recipe, render_chat


class FakeTok:
    bos_token, bos_token_id, eos_token = "<BOS>", 1, "<EOT>"

    def __call__(self, text):
        return {"input_ids": [1, 7]}  # adds BOS, like the Cohere tokenizers

    def apply_chat_template(self, msgs, tokenize=False, add_generation_prompt=False):
        out = "<BOS><SYS>preamble<EOT>"
        for m in msgs:
            out += f"<{m['role'].upper()}>{m['content']}<EOT>"
        return out + ("<ASSISTANT>" if add_generation_prompt else "")


def test_render_chat_strips_bos_and_keeps_end_tokens():
    tok = FakeTok()
    rows = [{"prompt": [{"role": "user", "content": "rate"}],
             "completion": [{"role": "assistant", "content": '{"overall_score": 0.8}'}]}]
    # FakeTok renders the assistant turn as "<ASSISTANT>...<EOT>", so the generation prompt is a prefix
    out = render_chat(rows, tok)[0]
    assert out["prompt"] == "<SYS>preamble<EOT><USER>rate<EOT><ASSISTANT>"
    assert out["completion"] == '{"overall_score": 0.8}<EOT>'


def test_render_chat_passes_plain_strings_through():
    rows = [{"prompt": "Q: x\nAnswer:", "completion": " y"}]
    assert render_chat(rows, FakeTok()) == rows


def test_recipe_warmup():
    assert Recipe().warmup_steps(1600) == 6  # 100 steps/epoch x 2 epochs x 3%


def test_latest_checkpoint_skips_incomplete(tmp_path):
    from lorde_forge.train import latest_checkpoint

    assert latest_checkpoint(tmp_path) is None
    for step, complete in ((200, True), (1000, True), (1200, False), (400, True)):
        (tmp_path / f"checkpoint-{step}").mkdir()
        if complete:
            (tmp_path / f"checkpoint-{step}" / "trainer_state.json").write_text("{}")
    assert latest_checkpoint(tmp_path) == str(tmp_path / "checkpoint-1000")


def test_aligntune_resumes_and_gets_the_val_split(tmp_path, monkeypatch):
    """AlignTune has no resume option: train_aligntune makes its internal SFTTrainer.train() resume."""
    import sys
    import types

    from lorde_forge import jsonl, train

    calls = {}

    class SFTTrainer:  # stands in for trl.SFTTrainer, which AlignTune calls with no arguments
        def train(self, resume_from_checkpoint=None):
            calls["resume"] = resume_from_checkpoint

    class AlignTuneTrainer:
        def __init__(self, **kw):
            calls["kwargs"] = kw
            self.config = types.SimpleNamespace(dataset=types.SimpleNamespace(name=kw["dataset_name"]))
            self.model = None
            self.dataset = self.eval_dataset = None

        def setup_model(self):
            self.model = object()

        def setup_data(self):
            self.train_dataset = jsonl.read(self.config.dataset.name)

        def train(self):
            calls["eval_rows"] = len(self.eval_dataset or [])
            calls["train_rows"] = len(self.dataset or [])
            SFTTrainer().train()

        def save_model(self, path):
            calls["saved"] = path

    monkeypatch.setitem(sys.modules, "trl", types.SimpleNamespace(SFTTrainer=SFTTrainer))
    monkeypatch.setitem(sys.modules, "aligntune", types.ModuleType("aligntune"))
    monkeypatch.setitem(sys.modules, "aligntune.core", types.ModuleType("aligntune.core"))
    monkeypatch.setitem(sys.modules, "aligntune.core.backend_factory",
                        types.SimpleNamespace(create_sft_trainer=lambda **kw: AlignTuneTrainer(**kw)))
    tok = types.SimpleNamespace(from_pretrained=lambda m: FakeTok())
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(AutoTokenizer=tok))
    monkeypatch.setattr(train, "precision", lambda: ("fp16", True, False))
    row = {"prompt": [{"role": "user", "content": "rate"}], "completion": [{"role": "assistant", "content": "{}"}]}
    jsonl.write(tmp_path / "train.jsonl", [row] * 5)
    jsonl.write(tmp_path / "val.jsonl", [row] * 2)
    out = tmp_path / "adapter"
    (out / "checkpoint-200").mkdir(parents=True)
    (out / "checkpoint-200" / "trainer_state.json").write_text("{}")
    key = train.resume_key("m", tmp_path / "train.jsonl", Recipe(), "aligntune")
    (out / "resume_key.json").write_text(__import__("json").dumps(key))  # same rows, recipe and base model
    train.train_aligntune("m", tmp_path / "train.jsonl", out, 5, Recipe(), val_path=tmp_path / "val.jsonl")
    assert calls["resume"] == str(out / "checkpoint-200")
    assert calls["eval_rows"] == 2 and calls["train_rows"] == 5
    assert calls["kwargs"]["eval_strategy"] == "steps" and calls["kwargs"]["save_total_limit"] == 2
    SFTTrainer().train()  # the patch is gone afterwards
    assert calls["resume"] is None


def _ckpt(out, step):
    (out / f"checkpoint-{step}").mkdir(parents=True)
    (out / f"checkpoint-{step}" / "trainer_state.json").write_text("{}")


def test_resume_key_tracks_rows_recipe_and_base(tmp_path):
    from lorde_forge import jsonl
    from lorde_forge.train import resume_key

    jsonl.write(tmp_path / "a.jsonl", [{"prompt": "p", "completion": "c"}])
    jsonl.write(tmp_path / "b.jsonl", [{"prompt": "p", "completion": "d"}])
    base = resume_key("m", tmp_path / "a.jsonl", Recipe(), "trl")
    assert base == resume_key("m", tmp_path / "a.jsonl", Recipe(), "trl")
    assert base["train_rows"] == 1
    assert base != resume_key("m", tmp_path / "b.jsonl", Recipe(), "trl")
    assert base != resume_key("m", tmp_path / "a.jsonl", Recipe(lr=2e-4), "trl")
    assert base != resume_key("other", tmp_path / "a.jsonl", Recipe(), "trl")


def test_prepare_resume_keeps_matching_checkpoints(tmp_path, capsys):
    import json

    from lorde_forge.train import prepare_resume

    out = tmp_path / "adapter"
    key = {"version": 1, "train_sha256": "aa", "recipe": {"lr": 1e-4, "targets": ["q_proj"]}}
    assert prepare_resume(out, key) is None  # nothing to resume; the key is written for the next run
    assert json.loads((out / "resume_key.json").read_text()) == key
    _ckpt(out, 200)
    _ckpt(out, 400)
    assert prepare_resume(out, json.loads(json.dumps(key))) == str(out / "checkpoint-400")
    assert (out / "checkpoint-200").exists() and "deleting" not in capsys.readouterr().out


def test_prepare_resume_wipes_checkpoints_of_other_data_or_without_key(tmp_path, capsys):
    import json

    from lorde_forge.train import prepare_resume

    out = tmp_path / "adapter"
    _ckpt(out, 200)
    (out / "adapter_config.json").write_text("{}")  # a finished adapter is not a checkpoint: kept
    key = {"version": 1, "train_sha256": "new", "recipe": {}}
    assert prepare_resume(out, key) is None  # older run without a key
    assert not (out / "checkpoint-200").exists() and (out / "adapter_config.json").exists()
    assert "no resume key" in capsys.readouterr().out
    _ckpt(out, 600)
    (out / "resume_key.json").write_text(json.dumps({**key, "train_sha256": "old"}))
    assert prepare_resume(out, key) is None
    assert not (out / "checkpoint-600").exists() and "train_sha256" in capsys.readouterr().out
    assert json.loads((out / "resume_key.json").read_text()) == key


def test_trl_wipes_mismatched_checkpoints_before_training(tmp_path, monkeypatch):
    import sys
    import types

    from lorde_forge import jsonl, train

    calls = {}

    class SFTTrainer:
        def __init__(self, **kw):
            pass

        def train(self, resume_from_checkpoint=None):
            calls["resume"] = resume_from_checkpoint

        def save_model(self, path):
            pass

    class DS(dict):
        def select_columns(self, cols):
            return self

    monkeypatch.setitem(sys.modules, "trl", types.SimpleNamespace(SFTTrainer=SFTTrainer, SFTConfig=lambda **kw: kw))
    monkeypatch.setitem(sys.modules, "peft", types.SimpleNamespace(LoraConfig=lambda **kw: kw))
    monkeypatch.setitem(sys.modules, "datasets", types.SimpleNamespace(load_dataset=lambda *a, **k: DS(train=[])))
    monkeypatch.setattr(train, "precision", lambda: ("fp32", False, False))
    monkeypatch.setattr(train, "torch_dtype", lambda: None)
    jsonl.write(tmp_path / "train.jsonl", [{"prompt": "p", "completion": "c"}])
    out = tmp_path / "adapter"
    _ckpt(out, 200)
    (out / "resume_key.json").write_text('{"train_sha256": "rows of another split"}')
    train.train_trl("m", tmp_path / "train.jsonl", None, out, 1, Recipe())
    assert calls["resume"] is None and not (out / "checkpoint-200").exists()
    _ckpt(out, 400)  # same data and recipe on the next attempt: resumed
    train.train_trl("m", tmp_path / "train.jsonl", None, out, 1, Recipe())
    assert calls["resume"] == str(out / "checkpoint-400")
