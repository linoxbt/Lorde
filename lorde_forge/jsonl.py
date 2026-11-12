"""JSONL helpers with resumable caches: every step can be killed and re-run."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


def read(path: str | Path) -> list[dict[str, Any]]:
    return list(iter_rows(path))


def iter_rows(path: str | Path) -> Iterator[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # a killed run can leave a half-written last line; skip it
                continue


def write(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(p)
    return n


class Appender:
    """Append rows one by one and flush, so progress survives a crash."""

    def __init__(self, path: str | Path, key: str = "uid", skip=None) -> None:
        """``skip(row)``: rows already in the file that do not count as done (e.g. failed calls)."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.key = key
        self.done: set[str] = {str(r.get(key)) for r in iter_rows(self.path) if not (skip and skip(r))}
        self._f = self.path.open("a", encoding="utf-8")

    def __contains__(self, uid: object) -> bool:
        return str(uid) in self.done

    def add(self, row: dict[str, Any]) -> None:
        self._f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._f.flush()
        self.done.add(str(row.get(self.key)))

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> Appender:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def write_json(path: str | Path, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
