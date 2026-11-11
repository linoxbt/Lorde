"""Identity of models and data sets, and the sidecar metadata that keys every prediction cache.

A cache (results/preds/<judge>.jsonl, results/downstream/preds_<set>.jsonl) is only reused when its
``<name>.meta.json`` says it was produced by the same model on the same items. Caches written before this
existed have no sidecar ("legacy"); they are reused only when the caller explicitly trusts them.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SAMPLE_CHUNKS = 16
CHUNK = 1 << 20


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def rows_sha(rows: Iterable[dict[str, Any]], fields: Sequence[str]) -> str:
    """Order-independent hash of the given fields of every row."""
    lines = sorted(json.dumps([r.get(f) for f in fields], ensure_ascii=False) for r in rows)
    return sha256("\n".join(lines))


def _file_digest(h, path: Path, sample: bool) -> None:
    size = path.stat().st_size
    h.update(f"{path.name}\0{size}\0".encode())
    with path.open("rb") as f:
        if not sample or size <= SAMPLE_CHUNKS * CHUNK:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
            return
        # Evenly spaced 1 MiB windows: a retrained (merged) model changes the weights everywhere, so
        # sampling detects it without reading gigabytes; the header and the tail are always included.
        for k in range(SAMPLE_CHUNKS):
            f.seek((size - CHUNK) * k // (SAMPLE_CHUNKS - 1))
            h.update(f.read(CHUNK))


def dir_sha(path: Path | str, sample: bool = False,
            suffixes: Sequence[str] = (".json", ".safetensors", ".bin", ".model", ".txt")) -> str:
    """Hash of the top-level files of a model or adapter directory (names, sizes and contents)."""
    p = Path(path)
    h = hashlib.sha256()
    for f in sorted(x for x in p.iterdir() if x.is_file() and x.suffix in suffixes):
        _file_digest(h, f, sample=sample and f.suffix == ".safetensors")
    return h.hexdigest()


def model_sha(path_or_id: str, base_dir: Path | None = None) -> tuple[str | None, str]:
    """(sha, source) for a local model directory or a Hugging Face model id."""
    for cand in (Path(path_or_id), (base_dir / path_or_id) if base_dir else None):
        if cand is not None and (cand / "config.json").exists():
            return dir_sha(cand, sample=True), f"local:{path_or_id}"
    if "/" in path_or_id and not path_or_id.startswith(("/", ".")):
        try:
            from huggingface_hub import HfApi

            return HfApi().model_info(path_or_id).sha, f"hub:{path_or_id}"
        except Exception:  # noqa: BLE001  (offline, gated, not a hub id)
            return None, f"unresolved:{path_or_id}"
    return None, f"unresolved:{path_or_id}"


def served_model(base_url: str, model: str, api_key: str | None = None, timeout: float = 10) -> dict[str, Any]:
    """The /v1/models entry of an OpenAI-compatible server (vLLM reports the weights path as ``root``)."""
    req = urllib.request.Request(base_url.rstrip("/") + "/models")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r).get("data", [])
    except Exception:  # noqa: BLE001
        return {}
    for m in data:
        if m.get("id") == model:
            return {k: m.get(k) for k in ("id", "root", "max_model_len") if m.get(k) is not None}
    return {}


def endpoint_model(base_url: str, model: str, api_key: str | None = None, sha: str | None = None,
                   path: str | None = None, base_dir: Path | None = None) -> dict[str, Any]:
    """Model identity for an endpoint: an explicit sha, else the hash of the weights it serves."""
    served = served_model(base_url, model, api_key)
    out: dict[str, Any] = {"served": served or None}
    if sha:
        out.update(sha=sha, source="--model-sha")
        return out
    src = path or served.get("root")
    if src:
        s, source = model_sha(src, base_dir)
        out.update(sha=s, source=source)
    else:
        out.update(sha=None, source="unknown")
    return out


# ----------------------------------------------------------------------------- sidecar metadata


def meta_path(cache: Path | str) -> Path:
    p = Path(cache)
    return p.with_name(p.name.removesuffix(".jsonl") + ".meta.json")


def read_meta(cache: Path | str) -> dict[str, Any] | None:
    p = meta_path(cache)
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except json.JSONDecodeError:
        return None


def write_meta(cache: Path | str, meta: dict[str, Any]) -> None:
    p = meta_path(cache)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({**meta, "written": datetime.now(UTC).isoformat(timespec="seconds")}, indent=1))
    os.replace(tmp, p)


def mismatch(old: dict[str, Any], new: dict[str, Any]) -> str | None:
    """Why ``old`` (a cache's meta) does not describe ``new``; None if it does. Keys missing from ``new``
    (unknown here, e.g. the model in a report-only run) are not compared."""
    for key, value in new.items():
        if key in ("written", "legacy_trusted", "note") or value is None:
            continue
        if key == "model" and isinstance(value, dict):
            o = old.get("model") or {}
            if value.get("sha") and o.get("sha") and value["sha"] != o["sha"]:
                return f"model changed ({str(o['sha'])[:12]} -> {value['sha'][:12]})"
            if value.get("sha") and not o.get("sha"):
                return "the cached run has no model fingerprint"
            continue
        if old.get(key) != value:
            return f"{key} changed"
    return None


def check(cache: Path | str, meta: dict[str, Any], trust_legacy: bool = False) -> tuple[bool, str]:
    """(usable, status) for an existing cache file against the expected ``meta``."""
    if not Path(cache).exists():
        return False, "missing"
    old = read_meta(cache)
    if old is None:
        return (True, "legacy (no fingerprint), trusted by --trust-legacy-cache") if trust_legacy else (
            False, "legacy cache without a fingerprint (pass --trust-legacy-cache to reuse it)")
    why = mismatch(old, meta)
    if why:
        return False, why
    if old.get("legacy_trusted"):
        return (True, "legacy (no fingerprint), trusted by --trust-legacy-cache") if trust_legacy else (
            False, "legacy cache trusted earlier; pass --trust-legacy-cache again to reuse it")
    return True, "valid"


def discard(cache: Path | str, why: str) -> None:
    """Move a stale cache aside (kept as <name>.stale, its meta too) and say so."""
    p = Path(cache)
    print(f"[cache] discarding {p.name}: {why}", flush=True)
    for f in (p, meta_path(p)):
        if f.exists():
            os.replace(f, f.with_name(f.name + ".stale"))
