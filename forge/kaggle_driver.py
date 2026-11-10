#!/usr/bin/env python
"""Headless Kaggle driver: runs the runbook's sessions without a notebook UI.

    python forge/kaggle_driver.py a      # Session A: envs, pool, teacher labels, splits, bench sets
    python forge/kaggle_driver.py b      # Session B: smoke train, full LoRA SFT, merge, save
    python forge/kaggle_driver.py ab     # A, then B if A's exit check passed
    python forge/kaggle_driver.py c      # Session C: judge eval, plug-in runs, downstream, private publish

Same commands as forge/kaggle_runbook.ipynb, run in the Kaggle kernel's own Python (huggingface_hub is
preinstalled there). Needs HF_TOKEN in the environment and HF_USER (default Oxlino). Every step resumes from
the private work repo <HF_USER>/lorde-work, and the driver uploads data/, results/, bench/ and logs/ there
after each step (data and logs also every 10 minutes during long steps), so progress is visible while it runs and a
killed session loses at most one step. A JSON progress record goes to live/progress.json in the same repo.
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
    ("train-smoke", "CUDA_VISIBLE_DEVICES=0 envs/train/bin/python forge/05_train_sft.py --max-samples 64 --epochs 1 --run smoke"),
    ("train", f"""CUDA_VISIBLE_DEVICES=0 envs/train/bin/python forge/05_train_sft.py {os.environ.get("TRAIN_ARGS", "")}
cat runs/lorde-v1/train_meta.json"""),
    ("save-model", None),
]

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
    ("downstream", """CUDA_VISIBLE_DEVICES=0 envs/train/bin/python forge/08_downstream.py all --sets ungated,teacher,lorde --include-base
cat results/downstream.md"""),
    ("publish-private", f"{PY} forge/09_publish.py --user {HF_USER}"),
]

SESSIONS = {"a": SESSION_A, "b": SESSION_B, "c": SESSION_C}


class Hub:
    def __init__(self) -> None:
        from huggingface_hub import HfApi

        self.api = HfApi()
        self.api.create_repo(WORK_REPO, repo_type="dataset", private=True, exist_ok=True)
        self.lock = threading.Lock()

    def pull(self) -> None:
        from huggingface_hub import snapshot_download

        snapshot_download(WORK_REPO, repo_type="dataset", local_dir=str(ROOT),
                          allow_patterns=["data/**", "results/**", "runs/**/merged/**", "runs/**/train_meta.json",
                                          "bench/**"])

    def push(self, folders=("data", "results", "bench", "logs"), message="sync") -> None:
        with self.lock:
            for base in folders:
                if (ROOT / base).exists():
                    try:
                        self.api.upload_folder(repo_id=WORK_REPO, repo_type="dataset", folder_path=str(ROOT / base),
                                               path_in_repo=base if base != "logs" else "live/logs",
                                               ignore_patterns=["*.tmp", "_input/**", "*.pid"], commit_message=message)
                    except Exception as e:  # a failed sync must never kill a long run
                        print(f"[driver] sync of {base} failed: {e}", flush=True)

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
    plan: list[tuple[str, str, str | None]] = []
    for s in which:
        seen_setup = any(n == "setup" for _, n, _ in plan)
        plan += [(s, n, c) for n, c in SESSIONS[s] if not (n == "setup" and seen_setup)]
    os.chdir(ROOT)
    os.environ.setdefault("VLLM_API_KEY", "lorde-local")
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    if not shutil.which("uv"):
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-U", "uv"], check=True)
    hub = Hub()
    hub.pull()
    record = {"session": which, "started": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "steps": []}
    for sess, name, script in plan:
        if only and name not in only and name != "setup":
            continue
        t0 = time.time()
        record["current"] = f"{sess}:{name}"
        hub.progress(record)
        print(f"\n===== [{sess}] {name} =====", flush=True)
        if name == "exit-check-a":
            ok, note = exit_check_a()
            rc = 0 if ok else 1
        elif name == "save-model":
            ok, note = save_model(hub)
            rc = 0 if ok else 1
        else:
            rc = run_bash(name, script or "", hub)
            note = ""
        step = {"step": f"{sess}:{name}", "rc": rc, "minutes": round((time.time() - t0) / 60, 1), "note": note}
        record["steps"].append(step)
        print(f"===== [{sess}] {name}: rc={rc} in {step['minutes']} min {note}", flush=True)
        if name != "setup":
            hub.push(message=f"{sess}:{name} rc={rc}")
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
