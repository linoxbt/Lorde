"""Step 1 leakage guards (audit B16)."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from lorde_forge import jsonl, variants

ROOT = Path(__file__).resolve().parent.parent


def load_pool():
    spec = importlib.util.spec_from_file_location("build_pool", ROOT / "forge" / "01_build_pool.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fallback_resumes_at_the_same_offset():
    bp = load_pool()
    dump = [{"title": f"A{i}", "url": f"u{i}", "text": "x"} for i in range(10)]

    def rows_api(name, progress):
        for row in dump[:4]:
            progress["n"] += 1
            yield row
        raise OSError("rate limited")

    def stream(name, skip=0):
        yield from dump[skip:]

    got = [a["title"] for a in bp._articles("x", rows_api=rows_api, stream=stream)]
    assert got == [f"A{i}" for i in range(10)]


def test_swapped_answers_come_from_the_same_split(tmp_path):
    cache = tmp_path / "data" / "cache"
    chunks, qa = [], []
    for use, prefix in (("judge", "g"), ("down_raw", "d"), ("down_eval", "e")):
        for i in range(30):
            cid = f"{prefix}-en-c{i:04d}"
            chunks.append({"chunk_id": cid, "lang": "en", "use": use, "title": cid, "url": None, "text": f"text {cid}"})
            qa.append({"chunk_id": cid, "qas": [{"question": f"q {cid}", "answer": f"answer from {use} {cid}"}]})
    jsonl.write(cache / "chunks.jsonl", chunks)
    jsonl.write(cache / "qa.jsonl", qa)
    jsonl.write(cache / "reward_prompts.jsonl", [{"pid": "r-en-0000", "lang": "en", "instruction": "i", "gold": "g",
                                                  "degraded": variants.pick_degraded("reward", "r-en-0000")}])
    env = {**os.environ, "LORDE_ROOT": str(tmp_path)}
    p = subprocess.run([sys.executable, str(ROOT / "forge" / "01_build_pool.py"), "--phase", "assemble"], env=env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    swapped = [r for r in jsonl.read(tmp_path / "data" / "pool.jsonl") if r["variant"] == "swapped_answer"]
    raw_swapped = [r for r in jsonl.read(tmp_path / "data" / "downstream_raw.jsonl") if r["variant"] == "swapped_answer"]
    assert swapped and raw_swapped
    assert all("answer from judge" in r["answer"] for r in swapped)
    assert all("answer from down_raw" in r["output"] for r in raw_swapped)
