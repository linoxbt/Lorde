"""Step 6 report on legacy (Session C) artefacts, explicit acceptance (B14), canonical verdicts (B13),
honest hardware (B5) and an article that survives missing numbers (B24)."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from lorde_forge import contract, jsonl

ROOT = Path(__file__).resolve().parent.parent


def load(rel):
    spec = importlib.util.spec_from_file_location(rel.replace("/", "_"), ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(rel, *args, env, codes=(0,)):
    p = subprocess.run([sys.executable, str(ROOT / rel), *args], env=env, capture_output=True, text=True,
                       timeout=600)
    assert p.returncode in codes, f"{rel}:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}"
    return p


def legacy_world(root: Path):
    """Artefacts as the pre-fix Session C writes them: no cache meta, raw teacher verdicts, old report."""
    test, preds = [], {"base": [], "lorde": []}
    for i in range(30):
        lang = ("en", "fr")[i % 2]
        if i % 3:
            item = {"kind": "reward", "instruction": f"Q{i}", "response": f"R{i}"}
            raw = json.dumps({"overall_score": 0.8 if i % 2 else 0.2})
            verdict = None
        else:
            item = {"kind": "grounding", "source_text": f"S{i}", "question": f"Q{i}", "answer": f"A{i}"}
            # the raw teacher reply has no verdict: training derived one from the score
            raw = json.dumps({"grounding_score": 0.9, "supported_claims": [], "unsupported_claims": []})
            verdict = None
        j = contract.parse_reply(item["kind"], raw)
        uid = f"x-{lang}-{i:04d}:gold"
        test.append({"uid": uid, "lang": lang, "variant": "gold", "group": f"x{i}", **item,
                     "prompt": contract.render({**item}), "teacher_score": j.score, "teacher_raw": raw,
                     "teacher_verdict": verdict, "teacher_decision": j.score >= 0.7})
        for name in preds:
            reply = json.loads(raw)
            if item["kind"] == "grounding":
                reply["verdict"] = "grounded"
            preds[name].append({"uid": uid, "raw": json.dumps(reply), "ok": True, "score": j.score,
                                "verdict": reply.get("verdict"), "latency_s": 0.1, "prompt_tokens": 10,
                                "completion_tokens": 5, "call_error": None})
    preds["base"][0] = {**preds["base"][0], "raw": None, "ok": False, "score": None, "call_error": "Timeout"}
    jsonl.write(root / "data" / "test.jsonl", test)
    for name, rows in preds.items():
        jsonl.write(root / "results" / "preds" / f"{name}.jsonl", rows)
    old_perf = {"p50_s": 0.2, "p95_s": 0.4, "judgments_per_s": 20.0, "completion_tokens_per_s": 100.0,
                "wall_s": 12.8, "n": 256, "concurrency": 16, "errors": 0, "mean_prompt_tokens": 700.0,
                "mean_completion_tokens": 60.0}
    jsonl.write_json(root / "results" / "judge_eval.json",
                     {"n_test": 30, "judges": {"base": {"perf": old_perf}, "lorde": {"perf": old_perf}},
                      "acceptance": []})
    jsonl.write(root / "data" / "labels.jsonl", [{"uid": t["uid"], "prompt_tokens": 700, "completion_tokens": 80}
                                                for t in test])
    return test


def test_report_only_on_legacy_session_c_artefacts(tmp_path):
    legacy_world(tmp_path)
    env = {**os.environ, "LORDE_ROOT": str(tmp_path)}
    judges = ["--judge", "base=openai/student@http://127.0.0.1:9/v1", "--judge", "lorde=openai/lorde@http://127.0.0.1:9/v1",
              "--gpu-hourly", "base=0.5", "--gpu-hourly", "lorde=0.5"]
    p = run("forge/06_judge_eval.py", *judges, "--report-only", env=env, codes=(1,))
    assert "--trust-legacy-cache" in p.stderr
    run("forge/06_judge_eval.py", *judges, "--report-only", "--trust-legacy-cache", "--hardware", "Tesla T4", env=env)
    rep = json.loads((tmp_path / "results" / "judge_eval.json").read_text())
    lorde = rep["judges"]["lorde"]
    assert lorde["cache"]["status"].startswith("legacy")
    assert lorde["perf"]["judgments_per_s"] == 20.0 and rep["hardware"]["name"] == "Tesla T4"
    # B13: the legacy reference verdict is re-derived from the teacher reply as in training ("grounded")
    assert lorde["by_kind"]["grounding"]["verdict_accuracy"] == 1.0
    # B15: the one transport failure is reported as such, not as a parse failure
    assert rep["judges"]["base"]["errors"] == {"call_errors": 1, "parse_failures": 0, "missing": 0}
    # B14: every blocking criterion is listed; missing inputs give met = None with a reason
    crit = {c["criterion"].split(" (")[0].split(" ≥")[0].split(" =")[0]: c for c in rep["acceptance"]}
    assert crit["Human decision agreement"]["met"] is None and "human labels" in crit["Human decision agreement"]["reason"]
    assert crit["Gate-usable rate, structured outputs"]["met"] is None
    assert crit["Per-language decision agreement"]["met"] is None  # 15 items per language < 20
    assert all("reason" in c for c in rep["acceptance"]) and rep["promotable"] is False
    md = (tmp_path / "results" / "judge_eval.md").read_text()
    assert "Promotable: no" in md and "Tesla T4" in md and "not measured" in md
    # audit 2, B7: the md says the predictions are a trusted legacy cache and how to recompute them
    assert "## Prediction caches" in md and "nothing proves the earlier session" in md
    assert "without `--trust-legacy-cache`" in md and lorde["cache"]["legacy_trusted"] is True

    run("docs/build_article.py", env=env)
    art = (tmp_path / "docs" / "HF_ARTICLE.md").read_text()
    assert "Lorde on 1× Tesla T4 (measured)" in art and "Lorde on 1× L4" not in art
    assert "not promotable" in art and "warm cache" in art


def test_acceptance_unit():
    ev = load("forge/06_judge_eval.py")
    vt = {"gate_usable_rate": 1.0, "decision_agreement": 0.95, "kappa": None, "spearman": None, "mae": None}
    lang = {lg: {"n": 25, "decision_agreement": 0.9} for lg in ev.config.LANG_CODES}
    r = {"judges": {"lorde": {"vs_teacher": vt, "by_kind": {}, "by_lang": lang, "perf": {}},
                    "lorde+so": {"vs_teacher": {**vt, "gate_usable_rate": 1.0}}}}
    crit = ev.acceptance(r, "lorde")
    by = {c["criterion"][:12]: c for c in crit}
    assert by["Cohen's κ vs"]["met"] is None and by["Per-language"]["met"] is True
    assert not ev.promotable(crit)
    lang["hi"] = {"n": 5, "decision_agreement": 0.2}  # too small to fail or pass
    assert {c["criterion"][:12]: c for c in ev.acceptance(r, "lorde")}["Per-language"]["met"] is None
    lang["fr"] = {"n": 40, "decision_agreement": 0.5}
    assert {c["criterion"][:12]: c for c in ev.acceptance(r, "lorde")}["Per-language"]["met"] is False
    assert all(c["met"] is None for c in ev.acceptance({"judges": {}}, "lorde") if c["blocking"])


def test_article_without_kappa_does_not_crash(tmp_path, monkeypatch):
    art = load("docs/build_article.py")
    monkeypatch.setattr(art.config, "RESULTS", tmp_path)
    jsonl.write_json(tmp_path / "judge_eval.json", {"judges": {"lorde": {"vs_teacher": {
        "gate_usable_rate": 1.0, "decision_agreement": 1.0, "kappa": None}}}})
    assert "κ undefined" in art.tldr()
    assert art.gpu_class("Tesla T4") == "t4" and art.gpu_class("NVIDIA L4") == "l4"
    assert art.gpu_class("NVIDIA A100-SXM4-40GB") is None


def test_downstream_within_2pct_is_two_sided():
    ds = load("forge/08_downstream.py")
    assert ds.within_2pct(0.5, 0.505) == (0.01, True)
    assert ds.within_2pct(0.5, 0.6)[1] is False  # better by 20% is not "within 2%"
    assert ds.within_2pct(0.5, 0.48)[1] is False
    assert ds.within_2pct(0.0, 0.3) == (None, None)  # not vacuously met
    md = ds.render_md({"metrics": {}, "train_rows": {}, "significance": {}, "by_lang": {},
                       "f1_relative_to_teacher": None, "within_2pct_of_teacher": None})
    assert "undefined" in md
