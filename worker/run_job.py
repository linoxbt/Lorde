"""Child process: run one CuratorKIT gating job exactly as a user would, with Lorde as the judge.

    python -m worker.run_job <job_dir>

Reads <job_dir>/job.json and <job_dir>/input.<ext>; writes <job_dir>/out/{passed.jsonl, rejected.jsonl,
report.md, judgments.jsonl, summary.json} plus CuratorKIT's manifest and lexsi_provenance.json.
Runs in its own process so the worker can stop it when a job is cancelled.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge.schemas import response_format  # noqa: E402


class TooManyRows(Exception):
    pass


def _lang(row: dict) -> str | None:
    v = row.get("lang") or row.get("language")
    return v.strip()[:16] if isinstance(v, str) and v.strip() else None


def stamp_ids(src: Path, fmt: str, dst_dir: Path, max_rows: int | None = None,
              langs: dict[int, str] | None = None, keep: int | None = None) -> tuple[Path, int]:
    """Copy the dataset with an ``id`` per row (CuratorKIT keeps it), so judgments map back to rows.

    Streams row by row (Parquet: batch by batch), so the input is never held in memory; stops with
    TooManyRows past ``max_rows``; records each row's ``lang`` (or ``language``) field into ``langs``.
    With ``keep`` only the first ``keep`` rows are copied (the org's daily quota allows no more); the rest
    are still counted. Returns (copy, rows in the input).
    """
    def kept(n: int) -> bool:
        return keep is None or n < keep

    def check(n: int) -> None:
        if max_rows is not None and n > max_rows:
            raise TooManyRows(f"dataset has more than {max_rows} rows (LORDE_JOB_MAX_ROWS); "
                              "split it into smaller jobs")

    def note(n: int, row) -> None:
        if langs is not None and isinstance(row, dict) and (lang := _lang(row)):
            langs[n] = lang

    if fmt == "jsonl":
        dst = dst_dir / "stamped.jsonl"
        n = 0
        with src.open(encoding="utf-8") as f, dst.open("w", encoding="utf-8") as out:
            for line in f:
                if not line.strip():
                    continue
                check(n + 1)
                row = json.loads(line)
                if isinstance(row, dict):
                    row["id"] = f"row-{n}"
                if kept(n):
                    note(n, row)
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
        return dst, n
    if fmt == "csv":
        dst = dst_dir / "stamped.csv"
        with src.open(encoding="utf-8", newline="") as f, dst.open("w", encoding="utf-8", newline="") as out:
            rd = csv.DictReader(f)
            fields = [c for c in (rd.fieldnames or []) if c != "id"] + ["id"]
            w = csv.DictWriter(out, fieldnames=fields)
            w.writeheader()
            n = 0
            for row in rd:
                check(n + 1)
                row["id"] = f"row-{n}"
                if kept(n):
                    note(n, row)
                    w.writerow(row)
                n += 1
        return dst, n
    if fmt == "parquet":
        import pyarrow as pa
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(src)
        check(pf.metadata.num_rows)
        dst = dst_dir / "stamped.parquet"
        writer = None
        n = 0
        try:
            for batch in pf.iter_batches(batch_size=4096):
                t = pa.Table.from_batches([batch])
                if "id" in t.column_names:
                    t = t.drop(["id"])
                if langs is not None:
                    col = next((c for c in ("lang", "language") if c in t.column_names), None)
                    if col:
                        for i, v in enumerate(t.column(col).to_pylist()):
                            note(n + i, {"lang": v})
                t = t.append_column("id", pa.array([f"row-{n + i}" for i in range(t.num_rows)]))
                rows = t.num_rows
                if keep is not None:
                    t = t.slice(0, max(0, keep - n))
                if writer is None:
                    writer = pq.ParquetWriter(dst, t.schema)
                writer.write_table(t)
                n += rows
        finally:
            if writer is not None:
                writer.close()
        if writer is None:  # empty file: keep the schema
            t = pf.schema_arrow.empty_table()
            pq.write_table(t, dst)
        return dst, n
    return src, -1


TEXT_CLIP = 4000


def _clip(v) -> str | None:
    return None if v is None else str(v)[:TEXT_CLIP]


def gate_notes(sample) -> dict[str, dict]:
    out = {}
    for p in sample.provenance_chain:
        if p.step_name in ("RewardGate", "HallucinationGate"):
            out[p.step_name] = p.notes or {}
    return out


def main() -> None:
    from curatorkit import Curator, CuratorConfig

    job_dir = Path(sys.argv[1])
    job = json.loads((job_dir / "job.json").read_text())
    gates, opts = job["config"]["gates"], job["config"]["options"]
    out_dir = job_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    max_rows = job.get("max_rows")
    quota_rows = job.get("quota_rows")  # the org's daily quota allows judging only this many rows
    langs: dict[int, str] = {}
    if job.get("input_path"):
        try:
            stamped, n_rows = stamp_ids(Path(job["input_path"]), job.get("format", "jsonl"), job_dir, max_rows, langs,
                                        keep=quota_rows)
        except TooManyRows as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(3)
        dataset = str(stamped)
    else:
        dataset, n_rows = job["hf_dataset"], -1  # Hub datasets: CuratorKIT reads at most max_rows samples
    samples = max_rows if quota_rows is None else min(x for x in (max_rows, quota_rows) if x is not None)
    concurrency = max(1, int(job.get("judge_concurrency") or 16))

    judge: Any = {"model": f"openai/{job['served_model']}", "api_base": job["vllm_base_url"],
             "api_key": os.environ.get("VLLM_API_KEY") or "EMPTY", "temperature": 0.1, "max_tokens": 512,
             "concurrency": concurrency}
    cfg = CuratorConfig(
        dataset=dataset,
        output_dir=str(out_dir / "curatorkit"),
        name=f"lorde_job_{job['id']}",
        hallucination_threshold=gates.get("grounding"),
        reward_threshold=gates.get("reward"),
        judge_llm=judge,
        # Structured outputs per gate: the worker calls Serve directly, so it asks for each schema itself.
        reward_llm=cast(Any, {"extra_body": {"response_format": response_format("reward")}}),
        hallucination_llm=cast(Any, {"extra_body": {"response_format": response_format("grounding")}}),
        judge_on_error="reject",
        judge_concurrency=concurrency,
        max_samples=samples,
        pii_pseudonymize=bool(opts.get("pii")),
        dedup=opts.get("dedup", "exact"),
        clean=False,
        export_formats=["alpaca"],
    )
    res = Curator(cfg).run()

    unstamped: dict[int, int] = {}  # Hub datasets carry no row-N id: number their samples in output order

    def row_idx(s) -> int:
        sid = str(s.id)
        if sid.startswith("row-") and sid[4:].isdigit():
            return int(sid[4:])
        return unstamped.setdefault(id(s), len(unstamped))

    def record(s, extra: dict) -> dict:
        notes = gate_notes(s)
        r = {"row": row_idx(s), "instruction": s.instruction, "input": s.input, "output": s.output, **extra}
        if "RewardGate" in notes:
            r["reward_score"] = notes["RewardGate"].get("reward_score")
        if "HallucinationGate" in notes:
            r["grounding_score"] = notes["HallucinationGate"].get("grounding_score")
            r["verdict"] = notes["HallucinationGate"].get("verdict")
        return r

    judgments = []
    for s, passed in [(s, True) for s in res.passed] + [(s, False) for s in res.rejected]:
        for step, gate, key in (("RewardGate", "reward", "reward_score"), ("HallucinationGate", "grounding",
                                                                             "grounding_score")):
            n = gate_notes(s).get(step)
            if n and key in n:
                idx = row_idx(s)
                judgments.append({"row_idx": idx, "gate": gate, "score": n[key], "lang": langs.get(idx),
                                  "decision": bool(n.get("passed", passed)), "verdict": n.get("verdict"),
                                  # row text; the worker stores it in judgments.parsed only when the org enabled
                                  # store_content (debug retention), never otherwise
                                  "text": {"instruction": _clip(s.instruction), "input": _clip(s.input),
                                           "output": _clip(s.output)}})

    with (out_dir / "passed.jsonl").open("w", encoding="utf-8") as f:
        for s in res.passed:
            f.write(json.dumps(record(s, {}), ensure_ascii=False) + "\n")
    with (out_dir / "rejected.jsonl").open("w", encoding="utf-8") as f:
        for s in res.rejected:
            f.write(json.dumps(record(s, {"rejection_reason": s.rejection_reason,
                                          "rejecting_step": s.rejecting_step}), ensure_ascii=False) + "\n")
    with (out_dir / "judgments.jsonl").open("w", encoding="utf-8") as f:
        for j in judgments:
            f.write(json.dumps(j) + "\n")

    reasons = Counter((r.rejection_reason or "?").split(":")[0] if not (r.rejection_reason or "").startswith(
        "judge_error") else r.rejection_reason for r in res.rejected)
    total = len(res.passed) + len(res.rejected)
    summary = {"rows_in": n_rows, "evaluated": total, "max_rows": max_rows, "passed": len(res.passed), "rejected": len(res.rejected),
               "pass_rate": round(len(res.passed) / total, 4) if total else None,
               "rejection_reasons": dict(reasons),
               "judge_errors": sum(v for k, v in reasons.items() if k.startswith("judge_error")),
               "wall_s": round(res.wall_clock_seconds, 1), "gates": gates, "options": opts}
    if quota_rows is not None and (n_rows > quota_rows or (n_rows < 0 and total >= quota_rows)):
        skipped = n_rows - quota_rows if n_rows >= 0 else None
        summary.update({"quota_rows": quota_rows, "not_evaluated": skipped, "notice": (
            f"{skipped if skipped is not None else 'Any further'} rows beyond the first {quota_rows} were not "
            "evaluated: the organization's daily judgment quota (LORDE_ORG_DAILY_JUDGMENTS) allowed no more "
            "today. They are in neither passed.jsonl nor rejected.jsonl; resubmit them after 00:00 UTC.")})
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "report.md").write_text(report_md(job, summary, res), encoding="utf-8")
    for name in ("manifest.json", "lexsi_provenance.json"):
        hits = list((out_dir / "curatorkit").rglob(name))
        if hits:
            (out_dir / name).write_bytes(hits[0].read_bytes())


def report_md(job: dict, s: dict, res) -> str:
    g = s["gates"]
    gates = ", ".join(f"{k} ≥ {v}" for k, v in g.items() if v is not None)
    lines = [f"# Lorde gating report · job {job['id']}\n",
             f"Model: `{job.get('model_revision')}` · gates: {gates} · CuratorKIT with Lorde as `judge_llm`\n",
             "| | count |", "|---|---|",
             f"| rows in | {s['rows_in'] if s['rows_in'] >= 0 else '–'} |", f"| evaluated | {s['evaluated']} |",
             f"| passed | {s['passed']} |", f"| rejected | {s['rejected']} |",
             f"| pass rate | {s['pass_rate'] * 100:.1f}% |" if s["pass_rate"] is not None else "| pass rate | – |",
             f"| judge errors | {s['judge_errors']} |", ""]
    if s.get("notice"):
        lines += [f"> **Not evaluated:** {s['notice']}", ""]
    lines += ["## Rejection reasons\n", "| reason | count |", "|---|---|"]
    lines += [f"| `{k}` | {v} |" for k, v in sorted(s["rejection_reasons"].items(), key=lambda x: -x[1])]
    lines += ["", "## Stage counts\n", "```", json.dumps(res.stage_counts, indent=2), "```", "",
              "Files: `passed.jsonl` (rows that cleared every gate, with scores), `rejected.jsonl` (with "
              "`rejection_reason`), `manifest.json` and `lexsi_provenance.json` (CuratorKIT provenance)."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
