"""Kaggle driver sync rules (audit B3, B22): human labels are never uploaded by the driver."""

import importlib.util
import os
import time
from pathlib import Path

from huggingface_hub.utils import filter_repo_objects

ROOT = Path(__file__).resolve().parent.parent


def load_driver():
    spec = importlib.util.spec_from_file_location("kaggle_driver", ROOT / "forge" / "kaggle_driver.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DATA_FILES = ["pool.jsonl", "test.jsonl", "human_labels.jsonl", "human_labels.jsonl.tmp", "human_sample.jsonl",
              "human_sample_key.jsonl", "sft/train.jsonl", "gated/_input/downstream_raw.jsonl", "labels.jsonl"]


def uploaded(patterns):
    return set(filter_repo_objects(DATA_FILES, ignore_patterns=patterns))


def test_push_never_uploads_human_labels():
    d = load_driver()
    regular = uploaded(d.push_ignore("data"))
    assert "human_labels.jsonl" not in regular and "human_labels.jsonl.tmp" not in regular
    assert "human_sample.jsonl" not in regular and "human_sample_key.jsonl" not in regular
    assert {"pool.jsonl", "test.jsonl", "sft/train.jsonl", "labels.jsonl"} <= regular
    after_splits = uploaded(d.push_ignore("data", human_sample=True))
    assert {"human_sample.jsonl", "human_sample_key.jsonl"} <= after_splits
    assert "human_labels.jsonl" not in after_splits
    assert "splits" in d.WRITES_HUMAN_SAMPLE and "judge-eval" in d.READS_HUMAN_LABELS


def test_push_passes_the_ignore_patterns(tmp_path, monkeypatch):
    d = load_driver()
    monkeypatch.setattr(d, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    calls = []

    class Api:
        def upload_folder(self, **kw):
            calls.append(kw)

    hub = d.Hub.__new__(d.Hub)
    hub.api, hub.lock = Api(), __import__("threading").Lock()
    hub.push(("data",), message="m")
    assert "human_labels.jsonl" in calls[0]["ignore_patterns"]


def test_latest_complete_checkpoint_only(tmp_path, monkeypatch):
    d = load_driver()
    monkeypatch.setattr(d, "ROOT", tmp_path)
    adapter = tmp_path / "runs" / "lorde-v1" / "adapter"
    old = time.time() - 600
    for step, complete in ((200, True), (400, True), (600, False)):
        c = adapter / f"checkpoint-{step}"
        c.mkdir(parents=True)
        (c / "adapter_model.safetensors").write_bytes(b"x")
        if complete:
            (c / "trainer_state.json").write_text("{}")
        for f in c.iterdir():
            os.utime(f, (old, old))
    assert d.latest_checkpoints() == [adapter / "checkpoint-400"]
    assert "runs/*/adapter/checkpoint-*/**" in d.pull_patterns(checkpoints=True)
    assert not any("checkpoint" in p for p in d.pull_patterns())
