"""Blind human sample and human labels.

The labeller must never see anything that reveals the answer: item uids embed the
variant (``g-en-c0050-q0:fabricated_numbers``), and ``variant`` / ``group`` name it
outright. So the sample the label tool reads (data/human_sample.jsonl) carries only an
opaque id (``h-<sha1(salt + uid)[:10]>``), kind, lang and the item text, in a
shuffled, stable order. The private key (data/human_sample_key.jsonl: opaque id ->
uid, variant, group) is never read by the label tool; evaluation uses it to map
labels back to test items.

Labels are keyed by (item id, labeller): several people may label the same item, and
the newest ``labeled_at`` per (item, labeller) wins. Legacy labels keyed by the
original uid are still accepted.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from . import jsonl

ITEM_FIELDS = ("instruction", "response", "source_text", "question", "answer")
SHOWN_FIELDS = ("uid", "kind", "lang", *ITEM_FIELDS)
LEAKY_FIELDS = ("variant", "group", "teacher_score", "teacher_raw", "teacher_verdict", "teacher_verdict_raw",
                "teacher_decision", "prompt", "chunk_id", "pid")
# Every name a variant can have (lorde_forge.variants and 01_build_pool); a uid ending in one is not blind.
VARIANT_NAMES = ("gold", "student", "truncated", "off_topic", "wrong_language", "padded_repetition",
                 "swapped_answer", "fabricated_numbers", "closed_book")


# lorde_forge.variants.fabricate_numbers appends " (<year>; <pct>%)" to an answer that has no number to change: a
# generation artefact that marks the item as fabricated_numbers to anyone who has seen a few (audit 2, S6).
FABRICATED_SUFFIX = re.compile(r" \((1[89]\d\d); (\d{1,2})%\)\s*$")


def text_tells(row: dict[str, Any]) -> list[str]:
    """Visible text that gives an item's variant away (the label is still determined by the text, but the
    labeller no longer has to read the source to know it)."""
    out = []
    for f in ("answer", "response"):
        if FABRICATED_SUFFIX.search(str(row.get(f) or "")):
            out.append(f"`{f}` ends with the fabricated-figure suffix")
    return out


def opaque_id(uid: str, salt: str) -> str:
    return "h-" + hashlib.sha1(f"{salt}{uid}".encode()).hexdigest()[:10]


def leaks(row: dict[str, Any]) -> list[str]:
    """Why ``row`` is not blind (empty list = blind)."""
    out = [f"field `{k}`" for k in LEAKY_FIELDS if k in row]
    uid = str(row.get("uid", ""))
    if ":" in uid or uid.split("-")[0] in ("g", "r") or uid.endswith(VARIANT_NAMES):
        out.append("a uid that is not opaque")
    return out


def is_blind(rows: Iterable[dict[str, Any]]) -> bool:
    return not any(leaks(r) for r in rows)


def blind(rows: Sequence[dict[str, Any]], salt: str | None = None,
          existing_key: Sequence[dict[str, Any]] = ()) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(blind sample, key rows) for test items. Ids already in ``existing_key`` are reused."""
    salt = salt or secrets.token_hex(8)
    known = {k["uid"]: k["id"] for k in existing_key}
    sample, key = [], []
    for r in rows:
        if r["uid"].startswith("h-") and "variant" not in r:  # already blind
            raise ValueError(f"item {r['uid']} is already blind; nothing to convert")
        hid = known.get(r["uid"]) or opaque_id(r["uid"], salt)
        sample.append({"uid": hid, **{k: r[k] for k in SHOWN_FIELDS[1:] if k in r}})
        key.append({"id": hid, "uid": r["uid"], "variant": r.get("variant"), "group": r.get("group")})
    if len({s["uid"] for s in sample}) != len(sample):
        raise ValueError("opaque id collision; re-run with another salt")
    # Hash order: positions reveal nothing (a stratified sample is round-robin over kind x lang x variant).
    sample.sort(key=lambda s: s["uid"])
    key.sort(key=lambda k: k["id"])
    return sample, key


def blind_keep(rows: Sequence[dict[str, Any]], salt: str | None,
               old_key: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Like ``blind``, but the key also keeps retired entries of ``old_key``, so labels made against an
    earlier sample still map to their items."""
    sample, key = blind(rows, salt, old_key)
    new_ids = {k["id"] for k in key}
    return sample, key + [k for k in old_key if k["id"] not in new_ids]


def write_blind(rows: Sequence[dict[str, Any]], sample_path: Path, key_path: Path, salt: str | None = None) -> int:
    sample, key = blind_keep(rows, salt, jsonl.read(key_path))
    jsonl.write(key_path, key)  # key first: never a blind sample without its key
    jsonl.write(sample_path, sample)
    return len(sample)


# ----------------------------------------------------------------------------- labels


def label_time(r: dict[str, Any]) -> str:
    return str(r.get("labeled_at") or r.get("ts") or "")


def label_key(r: dict[str, Any]) -> tuple[str, str]:
    return str(r.get("uid")), str(r.get("labeler") or "")


def merge_labels(*sources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union of label files; the newest ``labeled_at`` per (item, labeller) wins."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for src in sources:
        for r in src:
            if r.get("uid") is None:
                continue
            k = label_key(r)
            if k not in best or label_time(r) >= label_time(best[k]):
                best[k] = r
    return sorted(best.values(), key=lambda r: (label_time(r), label_key(r)))


def load_key(key_path: Path | str | None) -> dict[str, str]:
    return {r["id"]: r["uid"] for r in jsonl.read(key_path)} if key_path else {}


def aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One reference label from one or more labellers: mean score (0.1 grid), majority verdict."""
    scores = [float(r["human_score"]) for r in rows if r.get("human_score") is not None]
    verdicts = [r["human_verdict"] for r in rows if r.get("human_verdict")]
    verdict = None
    if verdicts:
        counts = Counter(verdicts)
        top = max(counts.values())
        latest = sorted(rows, key=label_time)
        verdict = next(r["human_verdict"] for r in reversed(latest) if counts.get(r.get("human_verdict")) == top)
    return {"human_score": round(sum(scores) / len(scores), 2) if scores else None, "human_verdict": verdict,
            "n_labelers": len({str(r.get("labeler") or "") for r in rows}),
            "kind": rows[-1].get("kind"), "lang": rows[-1].get("lang")}


def load_labels(labels_path: Path | str, key_path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """{test uid: aggregated label}. Opaque ids are mapped back through the key; labels keyed by the
    original uid (before blinding) are accepted as they are. Unmappable opaque ids are dropped."""
    key = load_key(key_path)
    by_uid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in merge_labels(jsonl.read(labels_path)):
        uid = str(r["uid"])
        if uid in key:
            uid = key[uid]
        elif uid.startswith("h-"):
            continue
        by_uid[uid].append(r)
    return {u: aggregate(rs) for u, rs in by_uid.items() if any(r.get("human_score") is not None for r in rs)}


def public_labels(rows: Iterable[dict[str, Any]], key_path: Path | str | None = None) -> list[dict[str, Any]]:
    """Labels safe to publish: test uids (via the key), scores and verdicts; no labeller names, no notes."""
    key = load_key(key_path)
    out = []
    for r in merge_labels(rows):
        out.append({"uid": key.get(str(r["uid"]), r["uid"]), "kind": r.get("kind"), "lang": r.get("lang"),
                    "human_score": r.get("human_score"), "human_verdict": r.get("human_verdict"),
                    "labeled_at": label_time(r) or None})
    return out
