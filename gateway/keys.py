"""API keys: ``lorde_sk_`` + 32 random bytes (base62). Only the prefix and SHA-256(key + pepper) are stored.

    python -m gateway.keys create --name ci --org demo [--scopes judge,jobs:read] [--rpm 600]
    python -m gateway.keys list
    python -m gateway.keys revoke <prefix>

This file-backed store suits one replica and the hackathon demo; Phase 2 swaps in the Supabase
``api_keys`` table behind the same ``KeyStore`` interface.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import string
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ALPHABET = string.ascii_letters + string.digits
KEY_PREFIX = "lorde_sk_"
PREFIX_LEN = 8  # characters of the random part kept as the public prefix
DEFAULT_SCOPES = ["judge"]
CACHE_TTL_S = 60.0


def _b62(n_bytes: int = 32) -> str:
    num = int.from_bytes(secrets.token_bytes(n_bytes), "big")
    out = []
    while num:
        num, r = divmod(num, 62)
        out.append(ALPHABET[r])
    return "".join(reversed(out)).rjust(43, "0")


def hash_key(key: str, pepper: str) -> str:
    return hashlib.sha256((key + pepper).encode()).hexdigest()


def prefix_of(key: str) -> str | None:
    if not key.startswith(KEY_PREFIX) or len(key) < len(KEY_PREFIX) + PREFIX_LEN + 16:
        return None
    return key[: len(KEY_PREFIX) + PREFIX_LEN]


@dataclass
class ApiKey:
    prefix: str
    key_hash: str
    name: str
    org_id: str
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    rpm: int = 600
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    revoked_at: str | None = None
    last_used_at: str | None = None


class KeyStore:
    """JSON file store with an in-process 60 s lookup cache; revocation purges the cache entry."""

    def __init__(self, path: str | Path, pepper: str) -> None:
        self.path = Path(path)
        self.pepper = pepper
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, ApiKey | None]] = {}

    def _load(self) -> dict[str, ApiKey]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text() or "{}")
        return {p: ApiKey(**v) for p, v in raw.items()}

    def _save(self, keys: dict[str, ApiKey]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({p: asdict(k) for p, k in keys.items()}, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def create(self, name: str, org_id: str, scopes: list[str] | None = None, rpm: int = 600) -> tuple[str, ApiKey]:
        key = KEY_PREFIX + _b62()
        rec = ApiKey(prefix=prefix_of(key), key_hash=hash_key(key, self.pepper), name=name, org_id=org_id,
                     scopes=scopes or list(DEFAULT_SCOPES), rpm=rpm)
        with self._lock:
            keys = self._load()
            keys[rec.prefix] = rec
            self._save(keys)
        return key, rec

    def revoke(self, prefix: str) -> bool:
        with self._lock:
            keys = self._load()
            if prefix not in keys:
                return False
            keys[prefix].revoked_at = datetime.now(UTC).isoformat()
            self._save(keys)
            self._cache.pop(prefix, None)
        return True

    def list(self) -> list[ApiKey]:
        return list(self._load().values())

    def verify(self, key: str) -> ApiKey | None:
        """The key record if ``key`` is valid and not revoked, else None (constant-time hash compare)."""
        prefix = prefix_of(key)
        if not prefix:
            return None
        now = time.monotonic()
        hit = self._cache.get(prefix)
        if hit and now - hit[0] < CACHE_TTL_S:
            rec = hit[1]
        else:
            rec = self._load().get(prefix)
            self._cache[prefix] = (now, rec)
        if rec is None or rec.revoked_at:
            return None
        if not hmac.compare_digest(rec.key_hash, hash_key(key, self.pepper)):
            return None
        return rec


def main() -> None:
    from .settings import Settings

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--org", required=True)
    c.add_argument("--scopes", default="judge")
    c.add_argument("--rpm", type=int, default=600)
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("prefix")
    args = ap.parse_args()
    s = Settings()
    s.check()
    store = KeyStore(s.keys_file, s.key_pepper)
    if args.cmd == "create":
        key, rec = store.create(args.name, args.org, args.scopes.split(","), args.rpm)
        print(f"{key}\n(shown once; prefix {rec.prefix}, org {rec.org_id}, scopes {rec.scopes}, rpm {rec.rpm})")
    elif args.cmd == "list":
        for k in store.list():
            print(f"{k.prefix}  {k.name:<16} org={k.org_id} scopes={','.join(k.scopes)} rpm={k.rpm} "
                  f"{'REVOKED ' + k.revoked_at if k.revoked_at else 'active'}")
    else:
        print("revoked" if store.revoke(args.prefix) else "no such prefix")


if __name__ == "__main__":
    main()
