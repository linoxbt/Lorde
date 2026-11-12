"""The HF article is generated from result files and says nothing they do not support (audit 2, B6 and
"Result validity"): no false phrases, κ with three decimals, golden-300 next to the full test set, the lenient
teacher, the groundedness regression, missed criteria, near-duplicates and the prediction-cache status."""

import os
import re
import subprocess
import sys
from pathlib import Path

from lorde_forge import jsonl

ROOT = Path(__file__).resolve().parent.parent

# Text that was false for the published run (or would be for any run with these files), and must never come back.
FORBIDDEN = [
    "human agreement is reported against that ceiling",
    "Human agreement is reported below",
    "Keeps the teacher honest",
    "keeps CuratorKIT's quality gates honest",
    "labels_32b.jsonl",
    "human_labels.jsonl",
    "via the Cohere API (calibration slice)",
    "no Cohere API key in the run",
    "so no test prompt appears in training",
    "Large API judges are reliable",
]


def kappas(text: str) -> list[str]:
    """Every κ value printed as "κ = x" or "κ x"."""
    return re.findall(r"κ\s*=?\s*(-?\d+\.\d+)", text)


def assert_honest(text: str) -> None:
    for phrase in FORBIDDEN:
        assert phrase not in text, phrase
    ks = kappas(text)
    assert ks and all(len(k.split(".")[1]) == 3 for k in ks), ks


def world(root: Path) -> None:
    """Result files shaped like the published run's, small."""
    w = lambda rel, obj: jsonl.write_json(root / rel, obj)  # noqa: E731
    vt = {"n": 937, "gate_usable_rate": 1.0, "decision_agreement": 0.9114, "kappa": 0.7479, "pass_rate": 0.79,
          "spearman": 0.864, "mae": 0.058}
    lang = {lg: {"n": 100, "decision_agreement": 0.9, "gate_usable_rate": 1.0, "kappa": 0.7} for lg in ("ar", "en")}
    lang["ar"]["decision_agreement"] = 0.8447
    var = {"grounding:gold": {"n": 10, "pass_rate": 1.0, "ref_pass_rate": 1.0}}
    perf = {"p50_s": 9.28, "p95_s": 13.29, "judgments_per_s": 1.677, "completion_tokens_per_s": 155.3, "n": 256,
            "concurrency": 16, "sample": {"method": "x"}, "mean_prompt_tokens": 836.6, "mean_completion_tokens": 92.6}
    judge = lambda v, cost, sha: {"vs_teacher": v, "by_kind": {"grounding": {"verdict_accuracy": 0.909}},  # noqa: E731
                                  "by_lang": lang, "by_variant": var, "perf": perf, "gpu_hourly": 0.5,
                                  "cost_per_1M": cost, "errors": {"call_errors": 0, "parse_failures": 0, "missing": 0},
                                  "cache": {"status": "legacy, trusted by --trust-legacy-cache",
                                            "model": {"sha": sha}}}
    acc = [{"criterion": "Cohen's κ vs teacher ≥ 0.75", "value": "0.748", "met": False, "blocking": True},
           {"criterion": "Human decision agreement ≥ teacher's − 3 pts", "value": "–", "met": None, "blocking": True,
            "reason": "no human labels for test items"},
           {"criterion": "Per-language decision agreement ≥ 85% (worst: ar, n = 103)", "value": "84.5%",
            "met": False, "blocking": True},
           {"criterion": "Decision agreement vs teacher ≥ 90%", "value": "91.1%", "met": True, "blocking": True}]
    w("results/judge_eval.json", {
        "n_test": 937, "hardware": {"name": "Tesla T4"},
        "templates": {"templates": {"reward": {"id": "ck/reward@1", "sha256": "1"}}},
        "judges": {"base": judge({**vt, "gate_usable_rate": 0.7417, "decision_agreement": 0.6446, "kappa": 0.1977},
                                 106.59, "af89d219b53e00"),
                   "lorde": judge(vt, 82.82, "d12ab6ec5c6d00")},
        "api_reference": {"model": "c4ai-aya-expanse-32b (Cohere API)", "mean_prompt_tokens": 534.1,
                          "mean_completion_tokens": 116.1, "price_per_1M_tokens": {"input": 0.5, "output": 1.5},
                          "cost_per_1M_judgments": 441.27, "token_source": "teacher labels (8B tokenizer)"},
        "acceptance": acc, "promotable": False})
    w("results/preds/lorde.meta.json", {"legacy_trusted": True, "written": "2026-11-11T16:53:40+00:00"})
    golden = {"n": 300, "gate_usable_rate": 1.0, "decision_agreement": 0.8733, "kappa": 0.6906, "mae": 0.0855}
    checks = [{"criterion": "Golden decision agreement vs teacher ≥ 90%", "met": False},
              {"criterion": "Golden κ vs teacher ≥ 0.75", "met": False},
              {"criterion": "Adversarial pass rate ≤ 5%", "met": False}]
    w("bench/results/lorde.json", {"vs_teacher_8b": golden, "adversarial_pass_rate": 0.46, "checks": checks,
                                   "adversarial_by_attack": {"injection": 0.65, "empty": 0.6}})
    w("bench/results/lorde+so.json", {"vs_teacher_8b": {**golden, "kappa": 0.6651}, "adversarial_pass_rate": 0.47,
                                      "checks": checks})
    sig = lambda b, c, p: {"n": 638, "mean_baseline": b, "mean_candidate": c, "delta": c - b, "p_value": p,  # noqa: E731
                           "significant": p < 0.05}
    w("results/downstream.json", {
        "metrics": {"base": {"f1_score": 0.4919, "lexical_groundedness": 0.9084},
                    "ungated": {"f1_score": 0.6639, "lexical_groundedness": 0.9408},
                    "teacher": {"f1_score": 0.6793, "lexical_groundedness": 0.9372},
                    "lorde": {"f1_score": 0.6814, "lexical_groundedness": 0.9303}},
        "train_rows": {"ungated": 2000, "teacher": 2000, "lorde": 2000}, "by_lang": {},
        "significance": {"lorde vs ungated": {"f1_score": sig(0.6639, 0.6814, 0.005994),
                                              "lexical_groundedness": sig(0.9408, 0.9303, 0.003996)},
                         "lorde vs teacher": {"f1_score": sig(0.6793, 0.6814, 0.703297),
                                              "lexical_groundedness": sig(0.9372, 0.9303, 0.022977)}},
        "f1_relative_to_teacher": 0.003, "within_2pct_of_teacher": True, "better_than_ungated_p05": True})
    pj = lambda passed, err, reasons, by: {"model": "m", "passed": passed, "rejected": 3600 - passed,  # noqa: E731
                                           "pass_rate": passed / 3600, "judge_error_rate": err, "wall_s": 1.0,
                                           "rejection_reasons": reasons, "pass_rate_by_variant": by}
    w("results/plugin_eval.json", {"n_raw": 3600, "raw_variants": {"gold": 1822},
                                   "judges": {"lorde": pj(2642, 0.0036, {"judge_error:JudgeParseError": 13},
                                                          {"fabricated_numbers": 0.7628, "closed_book": 0.7183}),
                                              "teacher": pj(2299, 0.1139, {"judge_error:JudgeParseError": 410},
                                                            {"fabricated_numbers": 0.6376, "closed_book": 0.4868})}})
    w("runs/lorde-v1/train_meta.json", {"recipe": {"epochs": 2, "lr": 0.0001, "lora_r": 16, "lora_alpha": 32,
                                                   "batch_size": 1, "grad_accum": 16, "max_len": 1536},
                                        "train_rows": 5281, "train_seconds": 21486.0})
    w("data/splits_summary.json", {"near_duplicates": {"threshold": 0.9, "test_prompts": 348,
                                                       "test_prompts_near_train": 17, "test_passages": 65,
                                                       "test_passages_near_train": 0,
                                                       "test_gold_responses_identical_to_train": 12}})
    pool, labels = [], []
    for variant, n, passing in (("swapped_answer", 10, 7), ("fabricated_numbers", 50, 47), ("gold", 10, 10)):
        for i in range(n):
            uid = f"g-{variant}-{i}"
            pool.append({"uid": uid, "kind": "grounding", "variant": variant})
            labels.append({"uid": uid, "kind": "grounding", "ok": True, "score": 1.0 if i < passing else 0.2,
                           "verdict": "grounded", "parsed": {"unsupported_claims": ["x"] if i == 0 else []}})
    jsonl.write(root / "data" / "pool.jsonl", pool)
    jsonl.write(root / "data" / "labels.jsonl", labels)
    jsonl.write(root / "data" / "human_sample.jsonl", [{"uid": f"h-{i}"} for i in range(200)])


def build(root: Path) -> str:
    env = {**os.environ, "LORDE_ROOT": str(root)}
    p = subprocess.run([sys.executable, str(ROOT / "docs" / "build_article.py")], env=env, capture_output=True,
                       text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    return (root / "docs" / "HF_ARTICLE.md").read_text()


def test_generated_article_is_honest(tmp_path):
    world(tmp_path)
    art = build(tmp_path)
    assert_honest(art)
    assert "κ = 0.748" in art and "κ = 0.75)" not in art  # three decimals, never rounded up to the target
    assert "**91.1%**" in art and "base: 64.5%" in art
    assert "golden-300 subset on **87.3%** (κ = 0.691)" in art  # golden-300 next to the full test set
    assert "imitation, not correctness" in art
    assert "70% of swapped answers" in art and "94% of fabricated numbers" in art  # the lenient teacher
    assert "**not promotable**" in art and "Cohen's κ vs teacher ≥ 0.75: 0.748" in art
    assert "84.5%, Arabic" in art and "Adversarial pass rate ≤ 5%: 46%" in art
    assert "passes **46%** of adversarial-100" in art and "47% with structured outputs" in art
    assert "less grounded" in art and "0.930 vs 0.941, p = 0.004" in art and "0.930 vs 0.937, p = 0.023" in art
    assert "410 of 3,600 rows (11.4%)" in art  # the teacher-gated set is depressed by parse errors
    assert "one training run (one seed) per set" in art and "2,000 rows per set" in art
    assert "**17 of 348**" in art and "**12** test gold responses" in art  # near-duplicates
    assert "0 hand labels" in art and "32B calibration slice was not run" in art
    assert "$441.27 (estimate)" in art and "not a measured bill" in art  # the API cost is a list-price estimate
    assert "earlier Kaggle session" in art and "--trust-legacy-cache" in art  # prediction-cache status (B7)
    assert "lower bound" in art  # train_seconds of a pre-fix train_meta.json is the last session only
    assert "<!--" not in art  # every placeholder filled


def test_generated_article_counts_resumed_training_sessions(tmp_path):
    world(tmp_path)
    jsonl.write_json(tmp_path / "runs" / "lorde-v1" / "train_meta.json",
                     {"recipe": {"epochs": 2}, "train_rows": 10, "train_seconds": 36000.0, "train_sessions": 2,
                      "train_seconds_all_sessions": True, "hardware": "Tesla T4"})
    art = build(tmp_path)
    assert "10.0 h of training on one Tesla T4 over 2 sessions" in art and "lower bound" not in art


def test_published_article_is_honest():
    """docs/HF_ARTICLE.md as committed (built from the published run's files)."""
    art = (ROOT / "docs" / "HF_ARTICLE.md").read_text()
    assert_honest(art)
    for fact in ("κ = 0.748", "91.1%", "64.5%", "90.9%", "87.3%", "κ = 0.691", "46%", "47%", "84.5%", "3600",
                 "0.681", "0.679", "0.664", "0.492", "0.930 vs 0.941, p = 0.004", "0.930 vs 0.937, p = 0.023",
                 "11.4%", "$82.82", "$441.27", "5×", "not promotable", "32B calibration slice was not run",
                 "0 hand labels", "70% of swapped answers", "94% of fabricated numbers", "76% of closed-book",
                 "99% of truncated"):
        assert fact in art, fact
