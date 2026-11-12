"""Run a judge endpoint over items with CuratorKIT's prompts; cache and parse its replies.

The cache (``path``) is keyed by model identity: its sidecar ``<name>.meta.json`` records the model
fingerprint, the structured-outputs flag, the template fingerprints and a hash of the items. A cache whose
sidecar does not match is moved aside and recomputed; one without a sidecar (written before fingerprints
existed) is reused only with ``trust_legacy``. Replies that failed at the transport level (``call_error``)
are never cached, so a one-off API error is retried next time instead of counting as unparseable forever.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import contract, fingerprint, jsonl, llm


def prompt_of(item: dict) -> str:
    return item.get("prompt") or contract.render(item)


def items_sha(items: list[dict]) -> str:
    return fingerprint.rows_sha(({"uid": r["uid"], "kind": r["kind"], "prompt": prompt_of(r)} for r in items),
                                ("uid", "kind", "prompt"))


def cache_meta(model: dict[str, Any] | None, items: list[dict] | None, structured: bool) -> dict[str, Any]:
    fp = contract.fingerprints()
    return {"model": model, "structured": structured,
            "items_sha": items_sha(items) if items is not None else None,
            "n_items": len(items) if items is not None else None,
            "templates": {k: v["sha256"] for k, v in fp["templates"].items()}}


def open_cache(path: Path, meta: dict[str, Any], trust_legacy: bool = False) -> str:
    """Make ``path`` safe to resume from: keep it if it matches ``meta``, else move it aside. Returns the status."""
    usable, status = fingerprint.check(path, meta, trust_legacy)
    if status == "missing":
        fingerprint.write_meta(path, meta)
        return "new"
    if not usable:
        fingerprint.discard(path, status)
        fingerprint.write_meta(path, meta)
        return f"recomputed ({status})"
    if status != "valid":  # trusted legacy: record what it was trusted for, but never as a clean match
        fingerprint.write_meta(path, {**meta, "legacy_trusted": True})
    return status


def predict(ep: llm.Endpoint, items: list[dict], structured: bool, path: Path | None,
            concurrency: int = 16, meta: dict[str, Any] | None = None, trust_legacy: bool = False) -> dict[str, dict]:
    """{uid: {raw, ok, score, verdict, latency_s, call_error, ...}}; replies are appended to ``path`` as they
    arrive (except call errors, which are returned but not cached)."""
    by_uid = {r["uid"]: r for r in items}

    def req(r: dict) -> llm.Request:
        return llm.Request(uid=r["uid"], messages=[{"role": "user", "content": prompt_of(r)}],
                           params=llm.judge_params(r["kind"], structured))

    def row(res: llm.Result) -> dict:
        j = contract.parse_reply(by_uid[res.uid]["kind"], res.text)
        return {**res.as_row(), "ok": j.ok and res.error is None, "score": j.score, "verdict": j.verdict}

    if path is None:
        res, _ = llm.run(ep, [req(r) for r in items], concurrency=concurrency, desc=ep.name)
        return {r.uid: row(r) for r in res}
    if meta is not None:
        open_cache(path, meta, trust_legacy)
    errors: dict[str, dict] = {}

    def on_result(res: llm.Result) -> None:
        if res.error is not None:
            errors[res.uid] = row(res)
        else:
            out.add(row(res))

    with jsonl.Appender(path, skip=lambda r: bool(r.get("call_error"))) as out:
        todo = [r for r in items if r["uid"] not in out]
        if todo:
            llm.run(ep, [req(r) for r in todo], concurrency=concurrency, desc=path.stem, on_result=on_result)
    cached = {r["uid"]: r for r in jsonl.read(path) if r["uid"] in by_uid and not r.get("call_error")}
    return {**{u: e for u, e in errors.items() if u not in cached}, **cached}


def error_counts(preds: dict[str, dict], uids: list[str]) -> dict[str, int]:
    """Transport failures (call errors), replies CuratorKIT could not parse, and items with no reply at all."""
    rows = [preds.get(u) for u in uids]
    return {"call_errors": sum(1 for p in rows if p and p.get("call_error")),
            "parse_failures": sum(1 for p in rows if p and not p.get("call_error") and not p.get("ok")),
            "missing": sum(1 for p in rows if not p)}


def as_metric_row(p: dict | None) -> dict:
    if not p:
        return {"ok": False, "score": None, "verdict": None}
    return {"ok": bool(p.get("ok")) and not p.get("call_error"), "score": p.get("score"),
            "verdict": p.get("verdict")}
