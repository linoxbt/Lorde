"""Kaggle driver sync rules (audit B3, B22): human labels are never uploaded by the driver. Audit 2: B7, B16, M9."""

import importlib.util
import json
import os
import subprocess
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
    assert "runs/*/adapter/resume_key.json" in d.pull_patterns(checkpoints=True)
    assert not any("checkpoint" in p for p in d.pull_patterns())


def run_parallel(d, tmp_path, cmd0, cmd1):
    script = "set -euo pipefail\n" + d.parallel(cmd0, cmd1, "test") + f"\ntouch {tmp_path}/after"
    t0 = time.time()
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
    return p, time.time() - t0


def test_parallel_step_fails_and_stops_the_other_gpu(tmp_path):
    """Audit B16: under set -e a failing `wait $p0` used to exit before `wait $p1`, orphaning GPU1's job."""
    d = load_driver()
    # GPU0 fails fast; GPU1 would run for 30 s and then write a marker
    p, took = run_parallel(d, tmp_path, "sleep 0.2; exit 3", f"sleep 30; touch {tmp_path}/gpu1_done")
    assert p.returncode != 0 and "stopped the other" in p.stderr
    assert took < 15 and not (tmp_path / "after").exists()
    time.sleep(0.5)
    assert not (tmp_path / "gpu1_done").exists()  # the sibling was killed, not left running
    # the same when GPU1 is the one that fails
    p, took = run_parallel(d, tmp_path, f"sleep 30; touch {tmp_path}/gpu0_done", "sleep 0.2; false")
    assert p.returncode != 0 and took < 15 and not (tmp_path / "gpu0_done").exists()
    # a slow failure after a fast success still fails the step
    p, _ = run_parallel(d, tmp_path, "true", "sleep 0.3; exit 1")
    assert p.returncode != 0 and not (tmp_path / "after").exists()
    # both succeed: the step goes on
    p, _ = run_parallel(d, tmp_path, "sleep 0.1", "sleep 0.2")
    assert p.returncode == 0 and (tmp_path / "after").exists()


def test_downstream_script_waits_for_both_gpus_safely():
    d = load_driver()
    assert "wait $p0 || r=1" in d.DOWNSTREAM and "wait $p1 || r=1" in d.DOWNSTREAM
    assert "\nwait $p0\n" not in d.DOWNSTREAM
    assert d.DOWNSTREAM.count("--equal-size --max-rows 2000 --epochs 1 --max-len 1024") == 4


def test_legacy_prediction_caches_are_opt_in(monkeypatch):
    """Audit B7: a reproduction recomputes step-6 predictions unless LORDE_TRUST_LEGACY_CACHE=1."""
    monkeypatch.delenv("LORDE_TRUST_LEGACY_CACHE", raising=False)
    d = load_driver()
    scripts = [s for sess in d.SESSIONS.values() for _, s in sess if isinstance(s, str)]
    assert not any("--trust-legacy-cache" in s for s in scripts)
    monkeypatch.setenv("LORDE_TRUST_LEGACY_CACHE", "1")
    d = load_driver()
    assert "--trust-legacy-cache" in dict(d.SESSION_D)["judge-timing"]


def test_runbook_runs_the_driver_steps():
    """Audit M9: the notebook takes its commands (downstream arguments included) from the driver, not a copy."""
    nb = json.loads((ROOT / "forge" / "kaggle_runbook.ipynb").read_text())
    code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    d = load_driver()
    assert "kaggle_driver.py" in code and "08_downstream.py" not in code
    for sess in "abc":
        for name, script in d.SESSIONS[sess]:
            if script is not None:
                assert f'step("{sess}", "{name}")' in code or name == "setup", (sess, name)
    assert 'step("a", "setup")' in code and 'step("d", "judge-timing")' in code
    assert 'step("c", "downstream")' in code
    assert "drv.Hub()" in code and "hub.push(" in code  # the driver's sync rules: never human labels
