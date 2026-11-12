"""API keys: ``lorde_sk_`` + 32 random bytes (base62). Only the prefix and SHA-256(key + pepper) are stored.

    python -m gateway.keys create --name ci --org <org id> [--scopes judge,jobs:read] [--rpm 600]
    python -m gateway.keys list [--org <org id>]
    python -m gateway.keys revoke <prefix>

With DATABASE_URL set the commands act on the Supabase ``api_keys`` table (``--org`` is an org uuid);
without it, on the JSON file named by LORDE_KEYS_FILE (one replica, local development).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import string
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ALPHABET = string.ascii_letters + string.digits
KEY_PREFIX = "lorde_sk_"
PREFIX_LEN = 8  # characters of the random part kept as the public prefix
DEFAULT_SCOPES = ["judge"]
CACHE_TTL_S = 60.0
MAX_RPM = 6000  # same cap as the api_keys_rpm_cap constraint (0005_hardening.sql)
# Generated keys are lorde_sk_ + 43 base62 characters; anything else is refused before any lookup.
KEY_FORMAT = re.compile(r"^lorde_sk_[A-Za-z0-9]{32,64}$")


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
    if not KEY_FORMAT.match(key):
        return None
    return key[: len(KEY_PREFIX) + PREFIX_LEN]


class KeyCache:
    """Bounded LRU with a TTL, keyed by key prefix. Misses (unknown prefixes) live in their own LRU, so a
    flood of random prefixes evicts only other misses, never real keys."""

    def __init__(self, size: int = 10_000, ttl_s: float = CACHE_TTL_S) -> None:
        self.size, self.ttl_s = size, ttl_s
        self._hits: OrderedDict[str, tuple[float, ApiKey]] = OrderedDict()
        self._misses: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, prefix: str) -> tuple[bool, ApiKey | None]:
        """(found, record): found is False when the caller must look the prefix up."""
        now = time.monotonic()
        with self._lock:
            hit = self._hits.get(prefix)
            if hit and now - hit[0] < self.ttl_s:
                self._hits.move_to_end(prefix)
                return True, hit[1]
            miss = self._misses.get(prefix)
            if miss is not None and now - miss < self.ttl_s:
                return True, None
        return False, None

    def put(self, prefix: str, rec: ApiKey | None) -> None:
        now = time.monotonic()
        with self._lock:
            table: OrderedDict = self._hits if rec is not None else self._misses
            (self._misses if rec is not None else self._hits).pop(prefix, None)
            table[prefix] = (now, rec) if rec is not None else now
            table.move_to_end(prefix)
            while len(table) > self.size:
                table.popitem(last=False)

    def pop(self, prefix: str, default=None):
        with self._lock:
            self._misses.pop(prefix, None)
            return self._hits.pop(prefix, default)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()
            self._misses.clear()

    def __len__(self) -> int:
        return len(self._hits) + len(self._misses)


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
    id: str | None = None
    # when the key's organization was created (Postgres only): new orgs get a lower daily quota (audit 2, S1)
    org_created_at: datetime | None = field(default=None, compare=False, repr=False)


class KeyStore:
    """JSON file store with an in-process 60 s lookup cache; revocation purges the cache entry."""

    def __init__(self, path: str | Path, pepper: str, cache_size: int = 10_000) -> None:
        self.path = Path(path)
        self.pepper = pepper
        self._lock = threading.Lock()
        self._cache = KeyCache(cache_size)

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
        prefix = prefix_of(key)
        assert prefix
        rec = ApiKey(prefix=prefix, key_hash=hash_key(key, self.pepper), name=name, org_id=org_id,
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

    async def alookup(self, key: str) -> ApiKey | None:
        return self.lookup(key)

    def lookup(self, key: str) -> ApiKey | None:
        """The record whose hash matches ``key``, revoked or not (constant-time compare); else None."""
        prefix = prefix_of(key)
        if not prefix:
            return None
        found, rec = self._cache.get(prefix)
        if not found:
            rec = self._load().get(prefix)
            self._cache.put(prefix, rec)
        if rec is None or not hmac.compare_digest(rec.key_hash, hash_key(key, self.pepper)):
            return None
        return rec


def _print(k: ApiKey) -> None:
    print(f"{k.prefix}  {k.name:<16} org={k.org_id} scopes={','.join(k.scopes)} rpm={k.rpm} "
          f"{'REVOKED ' + k.revoked_at if k.revoked_at else 'active'}")


async def _pg(args, s) -> None:
    from .db import PgKeyStore, make_pool

    pool = make_pool(s.database_url, max_size=1)
    await pool.open()
    try:
        store = PgKeyStore(pool, s.key_pepper)
        if args.cmd == "create":
            key, rec = await store.acreate(args.name, args.org, args.scopes.split(","), args.rpm, actor="cli")
            print(f"{key}\n(shown once; prefix {rec.prefix}, org {rec.org_id}, scopes {rec.scopes}, rpm {rec.rpm})")
        elif args.cmd == "list":
            for k in await store.alist(args.org):
                _print(k)
        else:
            print("revoked" if await store.arevoke(args.prefix, actor="cli") else "no such active prefix")
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> None:
    from .settings import Settings

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--org", required=True)
    c.add_argument("--scopes", default="judge")
    c.add_argument("--rpm", type=int, default=600)
    ls = sub.add_parser("list")
    ls.add_argument("--org")
    r = sub.add_parser("revoke")
    r.add_argument("prefix")
    args = ap.parse_args(argv)
    if args.cmd == "create" and not 1 <= args.rpm <= MAX_RPM:
        ap.error(f"--rpm must be between 1 and {MAX_RPM}")
    s = Settings()
    s.check()
    if s.database_url:
        asyncio.run(_pg(args, s))
        return
    store = KeyStore(s.keys_file, s.key_pepper)
    if args.cmd == "create":
        key, rec = store.create(args.name, args.org, args.scopes.split(","), args.rpm)
        print(f"{key}\n(shown once; prefix {rec.prefix}, org {rec.org_id}, scopes {rec.scopes}, rpm {rec.rpm})")
    elif args.cmd == "list":
        for k in store.list():
            if not args.org or k.org_id == args.org:
                _print(k)
    else:
        print("revoked" if store.revoke(args.prefix) else "no such prefix")


if __name__ == "__main__":
    main()
