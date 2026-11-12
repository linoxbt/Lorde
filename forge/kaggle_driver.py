#!/usr/bin/env python
"""Headless Kaggle driver: runs the runbook's sessions without a notebook UI.

    python forge/kaggle_driver.py a      # Session A: envs, pool, teacher labels, splits, bench sets
    python forge/kaggle_driver.py b      # Session B: smoke train, full LoRA SFT, merge, save
    python forge/kaggle_driver.py ab     # A, then B if A's exit check passed
    python forge/kaggle_driver.py c      # Session C: judge eval, plug-in runs, downstream, private publish
    python forge/kaggle_driver.py d      # Session D: step-6 timings and predictions, downstream, private publish

forge/kaggle_runbook.ipynb runs these same step scripts (it imports this file), here in the Kaggle kernel's own
Python (huggingface_hub is preinstalled there). Needs HF_TOKEN in the environment; HF_USER (default Oxlino),
WORK_REPO, HUMAN_LANGS, DOWNSTREAM_ARGS, TRAIN_ARGS / TRAIN_BUDGET_H and LORDE_TRUST_LEGACY_CACHE=1 (reuse step-6
prediction caches that have no weights fingerprint; off by default, so a reproduction recomputes them) are
optional. Every step resumes from
the private work repo <HF_USER>/lorde-work, and the driver uploads data/, results/, bench/ and logs/ there
after each step (data and logs also every 10 minutes during long steps, plus the newest complete training
checkpoint while training), so progress is visible while it runs and a killed session loses at most one step
(training resumes from its checkpoint). A JSON progress record goes to live/progress.json in the same repo.

Human labels belong to the label tool: the driver never uploads data/human_labels.jsonl (its older local copy
would overwrite newer labels), uploads the human sample and its key only after the step that writes them, and
pulls the latest labels again right before the steps that read them.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HF_USER = os.environ.get("HF_USER", "Oxlino")
WORK_REPO = os.environ.get("WORK_REPO", f"{HF_USER}/lorde-work")
HUMAN_LANGS = os.environ.get("HUMAN_LANGS", "en,fr,es")
SERVE = "export PATH=$PWD/envs/serve/bin:$PATH"
PY = "envs/data/bin/python"

SETUP = """
mkdir -p /tmp/lorde-envs && ln -sfn /tmp/lorde-envs envs
for env in serve data train; do [ -x envs/$env/bin/python ] || uv venv -q -p 3.11 envs/$env; done
uv pip install -q --python envs/serve/bin/python --torch-backend=auto -r requirements/forge-serve.txt
uv pip install -q --python envs/data/bin/python -r requirements/forge-data.txt
uv pip install -q --python envs/train/bin/python --torch-backend=auto -r requirements/forge-train.txt
uv cache clean -q || true
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
for env in serve train; do
  envs/$env/bin/python -c "
import torch, sys
ok = torch.cuda.is_available() and torch.cuda.device_count() == 2
print('$env env: torch', torch.__version__, 'CUDA', torch.version.cuda, 'GPUs', torch.cuda.device_count(), 'OK' if ok else 'PROBLEM')
sys.exit(0 if ok else 1)"
done
envs/serve/bin/python -c "import vllm; print('vllm', vllm.__version__)"
envs/data/bin/python -c "import curatorkit; print('curatorkit', curatorkit.__version__)"
envs/train/bin/python -c "import aligntune, trl; print('trl', trl.__version__)"
df -h /kaggle/working /tmp 2>/dev/null | sed 1d || true
"""

SESSION_A = [
    ("setup", SETUP),
    ("pool-teacher", f"""{SERVE}
bash forge/00_serve.sh teacher
{PY} forge/01_build_pool.py --phase teacher --teacher-model openai/teacher --teacher-base http://localhost:8000/v1"""),
    ("pool-student", f"""{SERVE}
bash forge/00_serve.sh stop && CUDA_VISIBLE_DEVICES=0 bash forge/00_serve.sh student
{PY} forge/01_build_pool.py --phase student --student-model openai/student --student-base http://localhost:8001/v1
{PY} forge/01_build_pool.py --phase assemble"""),
    ("teacher-label", f"""{SERVE}
bash forge/00_serve.sh stop && bash forge/00_serve.sh teacher
{PY} forge/02_teacher_label.py --model openai/teacher --base http://localhost:8000/v1 --workers 32
cat results/labels_summary.json"""),
    ("exit-check-a", None),
    ("splits", f"""{PY} forge/03_make_splits.py --human-langs {HUMAN_LANGS}
{PY} bench/build_sets.py"""),
    ("calibration-32b", f"""if [ -n "${{COHERE_API_KEY:-}}" ]; then
  {PY} forge/02_teacher_label.py --model c4ai-aya-expanse-32b \\
    --base https://api.cohere.ai/compatibility/v1 --api-key-env COHERE_API_KEY \\
    --sample-from bench/data/golden-300.jsonl --max-calls 300 --workers 4 --out data/labels_32b.jsonl
  {PY} bench/build_sets.py
else echo "no COHERE_API_KEY: 32B calibration skipped"; fi
{SERVE} && bash forge/00_serve.sh stop"""),
]

SESSION_B = [
    ("setup", SETUP),
    # 160 rows = 10 optimizer steps at the default batch 1 x 16, enough to time a step.
    ("train-smoke", "CUDA_VISIBLE_DEVICES=0 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True envs/train/bin/python forge/05_train_sft.py --max-samples 160 --epochs 1 --run smoke"),
    ("train", lambda: f"""CUDA_VISIBLE_DEVICES=0 PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True envs/train/bin/python forge/05_train_sft.py {train_args()}
cat runs/lorde-v1/train_meta.json"""),
    ("save-model", None),
]

# Downstream (step 8): train the three sets on both T4s at once, equal size and capped so the step fits a session
# (~32 s per optimiser step at batch 1 x 16 on a T4), then generate and score on one GPU.
# These are the settings behind the published downstream numbers (forge/kaggle_runbook.ipynb runs this same script).
DOWNSTREAM_ARGS = os.environ.get("DOWNSTREAM_ARGS", "--equal-size --max-rows 2000 --epochs 1 --max-len 1024")
_DS = "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True envs/train/bin/python forge/08_downstream.py"


def parallel(cmd0: str, cmd1: str, what: str = "job") -> str:
    """Bash for two background commands (one per GPU). The step runs under ``set -e``, where a bare
    ``wait $p0`` that fails would exit before ``wait $p1`` and leave the other GPU's job orphaned: here the first
    failure stops the sibling (its whole process group, so worker processes go too), both are reaped, and the step
    fails."""
    return f"""set -m
{{ {cmd0}; }} &
p0=$!
{{ {cmd1}; }} &
p1=$!
set +m
r=0
wait -n || r=1
if [ $r -ne 0 ]; then kill -- -$p0 -$p1 2>/dev/null || true; fi
wait $p0 || r=1
wait $p1 || r=1
if [ $r -ne 0 ]; then echo "[driver] {what} failed on one GPU; stopped the other" >&2; exit 1; fi"""


DOWNSTREAM = parallel(
    f"CUDA_VISIBLE_DEVICES=0 {_DS} train --sets ungated,teacher,lorde --only-sets ungated,lorde {DOWNSTREAM_ARGS}",
    f"CUDA_VISIBLE_DEVICES=1 {_DS} train --sets ungated,teacher,lorde --only-sets teacher {DOWNSTREAM_ARGS}",
    "downstream training") + f"""
CUDA_VISIBLE_DEVICES=0 {_DS} generate --sets ungated,teacher,lorde --include-base {DOWNSTREAM_ARGS}
CUDA_VISIBLE_DEVICES=0 {_DS} score --sets ungated,teacher,lorde --include-base {DOWNSTREAM_ARGS}
cat results/downstream.md"""

SESSION_C = [
    ("setup", SETUP),
    ("judge-eval", f"""{SERVE}
bash forge/00_serve.sh stop
CUDA_VISIBLE_DEVICES=0 bash forge/00_serve.sh student
CUDA_VISIBLE_DEVICES=1 bash forge/00_serve.sh lorde
{PY} forge/06_judge_eval.py --judge base=openai/student@http://localhost:8001/v1 \\
  --judge lorde=openai/lorde@http://localhost:8002/v1 --gpu-hourly base=0.50 --gpu-hourly lorde=0.50 --structured
{PY} bench/run_bench.py --judge lorde=openai/lorde@http://localhost:8002/v1 || true
{PY} bench/run_bench.py --judge lorde=openai/lorde@http://localhost:8002/v1 --structured || true
{PY} forge/07_curatorkit_plugin.py --judge base=openai/student@http://localhost:8001/v1 \\
  --judge lorde=openai/lorde@http://localhost:8002/v1
{PY} forge/07_curatorkit_plugin.py --judge lorde=openai/lorde@http://localhost:8002/v1 --structured"""),
    ("plugin-teacher", f"""{SERVE}
bash forge/00_serve.sh stop && bash forge/00_serve.sh teacher
{PY} forge/07_curatorkit_plugin.py --judge teacher=openai/teacher@http://localhost:8000/v1
bash forge/00_serve.sh stop"""),
    ("downstream", DOWNSTREAM),
    ("publish-private", f"{PY} forge/09_publish.py --user {HF_USER}"),
]

def train_args() -> str:
    """Epochs (and, if needed, a row cap) that fit TRAIN_BUDGET_H, timed from the smoke run."""
    if os.environ.get("TRAIN_ARGS"):
        return os.environ["TRAIN_ARGS"]
    budget = float(os.environ.get("TRAIN_BUDGET_H", "5.5")) * 3600
    try:
        smoke = json.loads((ROOT / "runs" / "smoke" / "train_meta.json").read_text())
        rows = sum(1 for _ in open(ROOT / "data" / "sft" / "train.jsonl"))
        per_row = max(smoke["train_seconds"] - 150, 30) / smoke["train_rows"]  # minus model load
    except Exception as e:
        print(f"[driver] no smoke timing ({e}); training 1 epoch", flush=True)
        return "--epochs 1"
    est2 = 2 * rows * per_row
    print(f"[driver] {per_row:.2f} s/row; 2 epochs of {rows} rows ~ {est2 / 3600:.1f} h (budget {budget / 3600:.1f} h)",
          flush=True)
    if est2 <= budget:
        return "--epochs 2"
    if est2 / 2 <= budget:
        return "--epochs 1"
    return f"--epochs 1 --max-samples {int(budget / per_row)}"


# Prediction caches written before fingerprints existed (Session C's) are reused only when LORDE_TRUST_LEGACY_CACHE=1.
# The published Session D run did that: its step-6 predictions are Session C's, stamped "legacy_trusted" in
# results/preds/*.meta.json with the weights Session D served. By default a reproduction recomputes them
# (06 moves an untrusted legacy cache aside to <judge>.jsonl.stale.* and asks the server again: 937 items per judge).
TRUST_LEGACY = " --trust-legacy-cache" if os.environ.get("LORDE_TRUST_LEGACY_CACHE") == "1" else ""

# Session D: redo what Session C could not finish, keeping its plug-in results: cold, stratified step-6 timings and
# predictions (recomputed unless LORDE_TRUST_LEGACY_CACHE=1), downstream, private publish.
SESSION_D = [
    ("setup", SETUP),
    ("judge-timing", f"""{SERVE}
bash forge/00_serve.sh stop
CUDA_VISIBLE_DEVICES=0 bash forge/00_serve.sh student
CUDA_VISIBLE_DEVICES=1 bash forge/00_serve.sh lorde
{PY} forge/06_judge_eval.py --judge base=openai/student@http://localhost:8001/v1 \\
  --judge lorde=openai/lorde@http://localhost:8002/v1 --gpu-hourly base=0.50 --gpu-hourly lorde=0.50 --structured{TRUST_LEGACY}
bash forge/00_serve.sh stop"""),
    ("downstream", DOWNSTREAM),
    ("publish-private", f"{PY} forge/09_publish.py --user {HF_USER}"),
]

SESSIONS = {"a": SESSION_A, "b": SESSION_B, "c": SESSION_C, "d": SESSION_D}

# Written by the label tool (labels) or by step 3 (key); pulled fresh before the steps that read them.
HUMAN_FILES = ("human_labels.jsonl", "human_sample_key.jsonl")
WRITES_HUMAN_SAMPLE = {"splits"}  # only after this step may the sample and its key be uploaded
READS_HUMAN_LABELS = {"judge-eval", "judge-timing", "publish-private"}
TRAIN_STEPS = {"train-smoke", "train"}


def push_ignore(base: str, human_sample: bool = False) -> list[str]:
    """ignore_patterns for upload_folder of ROOT/<base> (patterns are relative to that folder)."""
    out = ["*.tmp", "_input/**", "*.pid"]
    if base == "data":
        out += ["human_labels.jsonl", "human_labels.jsonl.*"]  # never: the label tool owns them
        if not human_sample:  # a stale local copy must not undo forge/04b_blind_sample.py on the hub
            out += ["human_sample.jsonl", "human_sample_key.jsonl"]
    return out


def pull_patterns(checkpoints: bool = False) -> list[str]:
    out = ["data/**", "results/**", "runs/**/merged/**", "runs/**/train_meta.json", "bench/**"]
    if checkpoints:
        # the key says what they belong to; the clock carries the training time of earlier sessions
        out += ["runs/*/adapter/checkpoint-*/**", "runs/*/adapter/resume_key.json", "runs/*/adapter/train_clock.json"]
    return out


def checkpoint_ready(ckpt: Path, quiet_s: float = 120) -> bool:
    """Complete once the trainer state is written and nothing in it changed for ``quiet_s`` seconds."""
    files = [f for f in ckpt.rglob("*") if f.is_file()]
    if not files or not (ckpt / "trainer_state.json").exists():
        return False
    return time.time() - max(f.stat().st_mtime for f in files) >= quiet_s


def latest_checkpoints(quiet_s: float = 120) -> list[Path]:
    out = []
    for adapter in sorted((ROOT / "runs").glob("*/adapter")):
        ready = [c for c in adapter.glob("checkpoint-*")
                 if c.name.split("-")[-1].isdigit() and checkpoint_ready(c, quiet_s)]
        if ready:
            out.append(max(ready, key=lambda c: int(c.name.split("-")[-1])))
    return out


class Hub:
    def __init__(self) -> None:
        from huggingface_hub import HfApi

        self.api = HfApi()
        self.api.create_repo(WORK_REPO, repo_type="dataset", private=True, exist_ok=True)
        self.lock = threading.Lock()

    def pull(self, checkpoints: bool = False) -> None:
        from huggingface_hub import snapshot_download

        snapshot_download(WORK_REPO, repo_type="dataset", local_dir=str(ROOT),
                          allow_patterns=pull_patterns(checkpoints))

    def pull_human_labels(self) -> None:
        """Fresh copies of the labels (written by the label tool, maybe minutes ago) and of the sample key."""
        from huggingface_hub import hf_hub_download

        for name in HUMAN_FILES:
            try:
                got = hf_hub_download(WORK_REPO, f"data/{name}", repo_type="dataset", force_download=True)
                (ROOT / "data").mkdir(exist_ok=True)
                shutil.copyfile(got, ROOT / "data" / name)
                print(f"[driver] pulled the latest data/{name}", flush=True)
            except Exception as e:  # noqa: BLE001  (no labels yet)
                print(f"[driver] data/{name} not pulled: {type(e).__name__}", flush=True)

    def push(self, folders=("data", "results", "bench", "logs"), message="sync", human_sample: bool = False) -> None:
        with self.lock:
            for base in folders:
                if (ROOT / base).exists():
                    try:
                        self.api.upload_folder(repo_id=WORK_REPO, repo_type="dataset", folder_path=str(ROOT / base),
                                               path_in_repo=base if base != "logs" else "live/logs",
                                               ignore_patterns=push_ignore(base, human_sample), commit_message=message)
                    except Exception as e:  # a failed sync must never kill a long run
                        print(f"[driver] sync of {base} failed: {e}", flush=True)

    def push_checkpoints(self) -> None:
        """Upload the newest complete training checkpoint per run, dropping older ones from the repo."""
        with self.lock:
            for ckpt in latest_checkpoints():
                adapter = ckpt.parent
                try:
                    self.api.upload_folder(repo_id=WORK_REPO, repo_type="dataset", folder_path=str(adapter),
                                           path_in_repo=adapter.relative_to(ROOT).as_posix(),
                                           allow_patterns=[f"{ckpt.name}/**", "resume_key.json", "train_clock.json"],
                                           delete_patterns=["checkpoint-*/**"],
                                           commit_message=f"checkpoint {ckpt.relative_to(ROOT).as_posix()}")
                    print(f"[driver] uploaded {ckpt.relative_to(ROOT)}", flush=True)
                except Exception as e:  # noqa: BLE001  (never kill training over a sync)
                    print(f"[driver] checkpoint upload failed: {e}", flush=True)

    def progress(self, record: dict) -> None:
        with self.lock:
            try:
                self.api.upload_file(path_or_fileobj=json.dumps(record, indent=1).encode(), path_in_repo="live/progress.json",
                                     repo_id=WORK_REPO, repo_type="dataset", commit_message="progress")
            except Exception as e:
                print(f"[driver] progress upload failed: {e}", flush=True)


def run_bash(name: str, script: str, hub: Hub) -> int:
    log = ROOT / "logs" / f"step_{name}.log"
    log.parent.mkdir(exist_ok=True)
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(600):
            hub.push(("data", "logs"), message=f"progress during {name}")
            if name in TRAIN_STEPS:
                hub.push_checkpoints()

    t = threading.Thread(target=heartbeat, daemon=True)
    t.start()
    with log.open("w") as fh:
        proc = subprocess.Popen(["bash", "-c", "set -euo pipefail\ncd " + str(ROOT) + "\n" + script],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:  # type: ignore[union-attr]
            sys.stdout.write(line)
            sys.stdout.flush()
            fh.write(line)
            fh.flush()
        rc = proc.wait()
    stop.set()
    return rc


def exit_check_a() -> tuple[bool, str]:
    p = ROOT / "results" / "labels_summary.json"
    if not p.exists():
        return False, "no results/labels_summary.json"
    s = json.loads(p.read_text())
    ok = (s.get("parse_ok_rate") or 0) >= 0.95 and len(s.get("score_bins") or {}) == 3
    return ok, f"parse_ok_rate={s.get('parse_ok_rate')} score_bins={s.get('score_bins')}"


def save_model(hub: Hub) -> tuple[bool, str]:
    run = ROOT / "runs" / "lorde-v1"
    if not (run / "merged" / "config.json").exists():
        return False, "no merged model"
    hub.api.upload_folder(repo_id=WORK_REPO, repo_type="dataset", folder_path=str(run), path_in_repo="runs/lorde-v1",
                          allow_patterns=["merged/**", "train_meta.json"], commit_message="merged Lorde model")
    return True, "uploaded runs/lorde-v1/merged"


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "a"
    only = set(os.environ.get("ONLY_STEPS", "").split(",")) - {""}
    plan: list[tuple[str, str, object]] = []
    for s in which:
        seen_setup = any(n == "setup" for _, n, _ in plan)
        plan += [(s, n, c) for n, c in SESSIONS[s] if not (n == "setup" and seen_setup)]
    os.chdir(ROOT)
    os.environ.setdefault("VLLM_API_KEY", "lorde-local")
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    if not shutil.which("uv"):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "uv"], check=True)
    hub = Hub()
    hub.pull(checkpoints="b" in which)
    record = {"session": which, "started": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "steps": []}
    for sess, name, script in plan:
        if only and name not in only and name != "setup":
            continue
        t0 = time.time()
        record["current"] = f"{sess}:{name}"
        hub.progress(record)
        print(f"\n===== [{sess}] {name} =====", flush=True)
        if name in READS_HUMAN_LABELS:
            hub.pull_human_labels()
        if name == "exit-check-a":
            ok, note = exit_check_a()
            rc = 0 if ok else 1
        elif name == "save-model":
            ok, note = save_model(hub)
            rc = 0 if ok else 1
        else:
            rc = run_bash(name, (script() if callable(script) else script) or "", hub)
            note = ""
        step = {"step": f"{sess}:{name}", "rc": rc, "minutes": round((time.time() - t0) / 60, 1), "note": note}
        record["steps"].append(step)
        print(f"===== [{sess}] {name}: rc={rc} in {step['minutes']} min {note}", flush=True)
        if name != "setup":
            hub.push(message=f"{sess}:{name} rc={rc}", human_sample=name in WRITES_HUMAN_SAMPLE and rc == 0)
        if rc != 0:
            record["current"] = None
            record["failed"] = f"{sess}:{name}"
            hub.push(("logs",), message="failure logs")
            hub.progress(record)
            subprocess.run(["bash", "-c", f"cd {ROOT} && {SERVE} && bash forge/00_serve.sh stop"], check=False)
            sys.exit(f"[driver] stopped at {sess}:{name} (rc={rc}); logs in {WORK_REPO}/live/logs")
    record["current"] = None
    record["finished"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    hub.progress(record)
    print("[driver] all steps done", flush=True)


if __name__ == "__main__":
    main()
