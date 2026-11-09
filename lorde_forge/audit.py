"""AuditKIT glue: per-sample scoring and paired-bootstrap significance tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def decision_run(name: str, uids: Sequence[str], ref: Sequence[bool], pred: Sequence[bool]):
    """An AuditKIT run scoring each item 1 if the judge's decision matches the reference."""
    from auditkit import evaluate
    from auditkit.sample import Sample

    lab = {True: "pass", False: "reject"}
    samples = [Sample(input=u, target=lab[r], id=u, actual_output=lab[p], tags=[name])
               for u, r, p in zip(uids, ref, pred)]
    return evaluate(samples, "precomputed", scorers="exact_match", experiment_name=name)


def decision_significance(uids: Sequence[str], ref: Sequence[bool], baseline: Sequence[bool],
                          candidate: Sequence[bool]) -> dict[str, Any]:
    from auditkit import compare

    rb = decision_run("baseline", uids, ref, baseline)
    rc = decision_run("candidate", uids, ref, candidate)
    return compare(rb, rc).significance("exact_match")


def answer_run(name: str, rows: Sequence[dict[str, Any]]):
    """Score generated answers: token F1 vs the gold answer, lexical groundedness vs the source.

    rows: {uid, question, answer (gold), context, output (generated), lang}
    """
    from auditkit import evaluate
    from auditkit.sample import Sample

    samples = [Sample(input=r["question"], target=r["answer"], id=r["uid"], actual_output=r["output"],
                      retrieval_context=[r["context"]], tags=[r.get("lang", "")], metadata={"lang": r.get("lang")})
               for r in rows]
    return evaluate(samples, "precomputed", scorers=["f1_score", "lexical_groundedness"], experiment_name=name)


def run_significance(baseline_run, candidate_run, metric: str) -> dict[str, Any]:
    from auditkit import compare

    return compare(baseline_run, candidate_run).significance(metric)


def per_sample(run, metric: str) -> dict[str, float]:
    """{sample_id: value} for one metric, read from a RunResult's predictions."""
    out: dict[str, float] = {}
    for p in run.predictions:
        sid = getattr(p, "sample_id", None)
        scores = (getattr(p, "metadata", None) or {}).get("scores", [])
        for s in scores:
            name = s.get("name") if isinstance(s, dict) else getattr(s, "name", None)
            value = s.get("value") if isinstance(s, dict) else getattr(s, "value", None)
            if name == metric and sid is not None and value is not None:
                out[str(sid)] = float(value)
    return out
