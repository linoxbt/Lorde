"""End-to-end smoke test of Forge steps 1-3, 6 and 7 against stub OpenAI servers (no GPU)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from stub_openai import serve

from lorde_forge import jsonl

ROOT = Path(__file__).resolve().parent.parent
LANGS = ["en", "fr"]

TEXT = {
    "en": "The Niger River is the main river of West Africa, about 4,180 km long. Its drainage basin covers "
          "2,117,700 square kilometres. Its source is in the Guinea Highlands. It runs in a crescent shape "
          "through Mali, Niger, on the border with Benin and then through Nigeria.",
    "fr": "Le Niger est le principal fleuve d'Afrique de l'Ouest, long d'environ 4 180 km. Son bassin couvre "
          "2 117 700 kilomètres carrés. Il prend sa source dans les hauts plateaux de Guinée et traverse le "
          "Mali, le Niger, puis le Nigeria avant de se jeter dans le golfe de Guinée.",
}


def run(step: str, *args: str, env: dict, codes: tuple[int, ...] = (0,)) -> str:
    script = ROOT / step if "/" in step else ROOT / "forge" / step
    p = subprocess.run([sys.executable, str(script), *args], env=env, capture_output=True, text=True, timeout=600)
    assert p.returncode in codes, f"{step} failed:\n{p.stdout[-3000:]}\n{p.stderr[-3000:]}"
    return p.stdout


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("lorde")
    cache = root / "data" / "cache"
    prompts, chunks = [], []
    from lorde_forge import variants

    for lang in LANGS:
        for i in range(12):
            pid = f"r-{lang}-{i:04d}"
            gold = f"A complete and helpful answer number {i} in {lang}, with enough detail to be useful."
            prompts.append({"pid": pid, "lang": lang, "instruction": f"Question {i} in {lang}?", "gold": gold,
                            "degraded": variants.pick_degraded("reward", pid)})
        for use, prefix, n in (("judge", "g", 6), ("down_raw", "d", 4), ("down_eval", "e", 2)):
            for i in range(n):
                chunks.append({"chunk_id": f"{prefix}-{lang}-c{i:04d}", "lang": lang, "use": use,
                               "title": f"T{i}", "url": None, "text": f"[{i}] " + TEXT[lang]})
    jsonl.write(cache / "reward_prompts.jsonl", prompts)
    jsonl.write(cache / "chunks.jsonl", chunks)

    teacher, t_url = serve("plain")
    student, s_url = serve("chatty")
    lorde, l_url = serve("fenced")
    env = {**os.environ, "LORDE_ROOT": str(root), "VLLM_API_KEY": "test", "OPENAI_API_KEY": "test",
           "LITELLM_LOG": "ERROR"}
    yield {"root": root, "env": env, "teacher": t_url, "student": s_url, "lorde": l_url}
    for s in (teacher, student, lorde):
        s.shutdown()


def test_forge_pipeline(world):
    env, root = world["env"], world["root"]
    run("01_build_pool.py", "--phase", "teacher", "--teacher-base", world["teacher"], "--workers", "4", env=env)
    run("01_build_pool.py", "--phase", "student", "--student-base", world["student"], "--workers", "4", env=env)
    run("01_build_pool.py", "--phase", "assemble", env=env)
    pool = jsonl.read(root / "data" / "pool.jsonl")
    assert {r["kind"] for r in pool} == {"reward", "grounding"}
    assert sum(r["variant"] == "gold" for r in pool) * 2 == len(pool)
    assert jsonl.read(root / "data" / "downstream_raw.jsonl") and jsonl.read(root / "data" / "downstream_eval.jsonl")

    run("02_teacher_label.py", "--base", world["teacher"], "--workers", "8", env=env)
    labels = jsonl.read(root / "data" / "labels.jsonl")
    assert len(labels) == len(pool) and all(r["ok"] for r in labels)

    run("03_make_splits.py", "--human-langs", "en,fr", "--test", "0.5", "--val", "0.1", "--human-n", "10", env=env)
    train = jsonl.read(root / "data" / "sft" / "train.jsonl")
    test = jsonl.read(root / "data" / "test.jsonl")
    assert train and test
    assert train[0]["completion"][0]["role"] == "assistant"
    assert not {r["uid"].split(":")[0] for r in train} & {r["uid"].split(":")[0] for r in test}
    human = jsonl.read(root / "data" / "human_sample.jsonl")
    assert human and "teacher_score" not in human[0]
    jsonl.write(root / "data" / "human_labels.jsonl",
                [{"uid": h["uid"], "kind": h["kind"], "lang": h["lang"], "human_score": 0.8,
                  "human_verdict": "grounded" if h["kind"] == "grounding" else None} for h in human])

    run("06_judge_eval.py", "--judge", f"base=openai/student@{world['student']}",
        "--judge", f"lorde=openai/lorde@{world['lorde']}", "--gpu-hourly", "base=0.5", "--gpu-hourly", "lorde=0.5",
        "--structured", "--perf-n", "8", "--concurrency", "4", env=env)
    rep = json.loads((root / "results" / "judge_eval.json").read_text())
    assert rep["judges"]["base"]["vs_teacher"]["gate_usable_rate"] == 0.0      # chatty: prose around JSON
    assert rep["judges"]["base+so"]["vs_teacher"]["gate_usable_rate"] == 1.0   # structured outputs fix it
    assert rep["judges"]["lorde"]["vs_teacher"]["decision_agreement"] == 1.0
    assert rep["judges"]["lorde"]["cost_per_1M"] is not None
    assert "human" in rep and rep["significance_decision_agreement"]
    md = (root / "results" / "judge_eval.md").read_text()
    assert "Acceptance criteria" in md and "Before / after" in md

    run("07_curatorkit_plugin.py", "--judge", f"lorde=openai/lorde@{world['lorde']}",
        "--judge", f"base=openai/student@{world['student']}", "--concurrency", "4", env=env)
    run("07_curatorkit_plugin.py", "--judge", f"base=openai/student@{world['student']}", "--structured",
        "--concurrency", "4", env=env)
    plug = json.loads((root / "results" / "plugin_eval.json").read_text())
    assert plug["judges"]["base"]["judge_error_rate"] == 1.0
    assert plug["judges"]["base+so"]["judge_error_rate"] == 0.0
    assert plug["judges"]["lorde"]["judge_error_rate"] == 0.0
    assert jsonl.read(root / "data" / "gated" / "lorde" / "passed.jsonl")
    assert list((root / "data" / "gated" / "lorde").glob("**/lexsi_provenance.json"))

    # Lorde Bench: frozen sets and the CI runner. The stub judge scores long answers high, so the
    # adversarial set (injections make answers longer) must trip the <= 5% gate and exit 1.
    run("bench/build_sets.py", env=env)
    adv = jsonl.read(root / "bench" / "data" / "adversarial-100.jsonl")
    assert adv and all(a["expected_decision"] is False for a in adv)
    assert {a["attack"] for a in adv} >= {"injection", "empty"}
    run("bench/run_bench.py", "--judge", f"lorde=openai/lorde@{world['lorde']}", "--structured",
        "--concurrency", "4", env=env, codes=(0, 1))
    bench = json.loads((root / "bench" / "results" / "lorde+so.json").read_text())
    assert bench["vs_teacher_8b"]["decision_agreement"] == 1.0
    assert bench["adversarial_pass_rate"] > 0.05 and bench["passed"] is False

    # Step 8 scoring with AuditKIT on precomputed answers (training and generation need a GPU).
    ev = jsonl.read(root / "data" / "downstream_eval.jsonl")
    outs = {"ungated": lambda r: "I am not sure.", "teacher": lambda r: r["answer"], "lorde": lambda r: r["answer"]}
    for name, f in outs.items():
        jsonl.write(root / "results" / "downstream" / f"preds_{name}.jsonl", [{**r, "output": f(r)} for r in ev])
    run("08_downstream.py", "score", env=env)
    down = json.loads((root / "results" / "downstream.json").read_text())
    assert down["metrics"]["lorde"]["f1_score"] == 1.0 and down["within_2pct_of_teacher"] is True

    run("09_publish.py", "--user", "tester", "--dry-run", env=env)
    card = (root / "results" / "cards" / "model_README.md").read_text()
    assert "license: cc-by-nc-4.0" in card and "Template fingerprints" in card
