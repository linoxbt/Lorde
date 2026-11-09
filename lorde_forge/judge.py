"""Run a judge endpoint over items with CuratorKIT's prompts; cache and parse its replies."""

from __future__ import annotations

from pathlib import Path

from . import contract, jsonl, llm


def prompt_of(item: dict) -> str:
    return item.get("prompt") or contract.render(item)


def predict(ep: llm.Endpoint, items: list[dict], structured: bool, path: Path | None,
            concurrency: int = 16) -> dict[str, dict]:
    """{uid: {raw, ok, score, verdict, latency_s, ...}}; appended to ``path`` as replies arrive."""
    by_uid = {r["uid"]: r for r in items}

    def req(r: dict) -> llm.Request:
        return llm.Request(uid=r["uid"], messages=[{"role": "user", "content": prompt_of(r)}],
                           params=llm.judge_params(r["kind"], structured))

    def row(res: llm.Result) -> dict:
        j = contract.parse_reply(by_uid[res.uid]["kind"], res.text)
        return {**res.as_row(), "ok": j.ok, "score": j.score, "verdict": j.verdict}

    if path is None:
        res, _ = llm.run(ep, [req(r) for r in items], concurrency=concurrency, desc=ep.name)
        return {r.uid: row(r) for r in res}
    with jsonl.Appender(path) as out:
        todo = [r for r in items if r["uid"] not in out]
        if todo:
            llm.run(ep, [req(r) for r in todo], concurrency=concurrency, desc=path.stem,
                    on_result=lambda res: out.add(row(res)))
    return {r["uid"]: r for r in jsonl.read(path) if r["uid"] in by_uid}


def as_metric_row(p: dict | None) -> dict:
    if not p:
        return {"ok": False, "score": None, "verdict": None}
    return {"ok": bool(p.get("ok")), "score": p.get("score"), "verdict": p.get("verdict")}
