"""Apply database migrations and bootstrap the first organization and admin key.

    python -m gateway.migrate

Runs every file in supabase/migrations once, in order, recording each in lorde_schema_migrations.
On plain Postgres (not Supabase), set LORDE_DB_SHIM=1 to create the minimal auth/storage schemas the
migrations reference. LORDE_BOOTSTRAP (JSON) creates an org and API keys from a prefix and a hash
only, so no plaintext key ever reaches the platform:
    {"org": "Lorde staging", "keys": [{"name": "admin", "prefix": "lorde_sk_XXXXXXXX", "hash": "<sha256>",
      "scopes": ["judge", "jobs:read", "jobs:write", "usage:read"]}]}
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "supabase" / "migrations"
SHIM = ROOT / "supabase" / "tests" / "supabase_shim.sql"


def run(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("select pg_advisory_lock(727272)")  # one migrator at a time across replicas
        try:
            c.execute("create table if not exists lorde_schema_migrations (name text primary key, "
                      "applied_at timestamptz not null default now())")
            done = {r[0] for r in c.execute("select name from lorde_schema_migrations")}
            if os.environ.get("LORDE_DB_SHIM") == "1" and "_shim" not in done:
                c.execute(SHIM.read_text())
                c.execute("insert into lorde_schema_migrations (name) values ('_shim')")
                print("[migrate] applied Supabase compatibility shim")
            for f in sorted(MIGRATIONS.glob("*.sql")):
                if f.name in done:
                    continue
                with c.transaction():
                    c.execute(f.read_text())
                    c.execute("insert into lorde_schema_migrations (name) values (%s)", (f.name,))
                print(f"[migrate] applied {f.name}")
            boot = os.environ.get("LORDE_BOOTSTRAP")
            if boot:
                bootstrap(c, json.loads(boot))
        finally:
            c.execute("select pg_advisory_unlock(727272)")


def bootstrap(c: psycopg.Connection, cfg: dict) -> None:
    name = cfg.get("org", "Lorde")
    row = c.execute("select id from orgs where name = %s order by created_at limit 1", (name,)).fetchone()
    org = row[0] if row else c.execute("insert into orgs (name) values (%s) returning id", (name,)).fetchone()[0]
    for k in cfg.get("keys", []):
        c.execute("select set_config('lorde.actor', 'bootstrap', false)")
        c.execute(
            "insert into api_keys (org_id, name, prefix, key_hash, scopes, rpm) values (%s, %s, %s, %s, %s, %s) "
            "on conflict (prefix) do nothing",
            (org, k["name"], k["prefix"], k["hash"], k.get("scopes", ["judge"]), k.get("rpm", 600)))
    print(f"[migrate] bootstrap: org '{name}' with {len(cfg.get('keys', []))} key(s) ensured")


if __name__ == "__main__":
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is required")
    run(url)
