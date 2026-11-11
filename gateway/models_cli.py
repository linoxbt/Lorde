"""Model registry (spec FR-12): candidate -> active -> retired, in the model_versions table.

    python -m gateway.models_cli promote <hf_repo> <revision> [--name NAME] [--bench bench.json]
    python -m gateway.models_cli list

``promote`` retires the active version and activates the given one in one transaction (registering it
first if it is new), and records model.promote in the audit log. Gateways pick the active version up
within a minute (LORDE_MODEL_REGISTRY=1, the default); LORDE_MODEL_REPO/REVISION stay the fallback when
no version is active. Point Lorde Serve at the same weights before promoting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg
from psycopg.types.json import Jsonb


def promote(url: str, repo: str, revision: str, name: str | None = None, bench: dict | None = None,
            actor: str = "cli") -> dict:
    with psycopg.connect(url) as c, c.transaction():
        c.execute("lock table model_versions in share row exclusive mode")  # one promotion at a time
        prev = c.execute("update model_versions set state = 'retired' where state = 'active' "
                         "returning hf_repo, revision").fetchall()
        row = c.execute("select id from model_versions where hf_repo = %s and revision = %s "
                        "order by created_at desc limit 1", (repo, revision)).fetchone()
        if row:
            c.execute("update model_versions set state = 'active', promoted_at = now(), "
                      "name = coalesce(%s, name), bench = coalesce(%s, bench) where id = %s",
                      (name, Jsonb(bench) if bench else None, row[0]))
            vid = row[0]
        else:
            vid = c.execute("insert into model_versions (name, hf_repo, revision, state, bench, promoted_at) "
                            "values (%s, %s, %s, 'active', %s, now()) returning id",
                            (name, repo, revision, Jsonb(bench) if bench else None)).fetchone()[0]
        c.execute("insert into audit_log (org_id, actor, action, target, detail) values "
                  "(null, %s, 'model.promote', %s, %s)",
                  (actor, f"{repo}@{revision}", Jsonb({"retired": [f"{r[0]}@{r[1]}" for r in prev]})))
    return {"id": str(vid), "active": f"{repo}@{revision}", "retired": [f"{r[0]}@{r[1]}" for r in prev]}


def list_versions(url: str) -> list[tuple]:
    with psycopg.connect(url) as c:
        return c.execute("select state, hf_repo, revision, name, promoted_at from model_versions "
                         "order by created_at").fetchall()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("promote")
    p.add_argument("repo")
    p.add_argument("revision")
    p.add_argument("--name")
    p.add_argument("--bench", help="Lorde Bench results JSON to store with the version")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    if a.cmd == "promote":
        bench = json.loads(open(a.bench, encoding="utf-8").read()) if a.bench else None
        print(json.dumps(promote(url, a.repo, a.revision, a.name, bench)))
    else:
        for r in list_versions(url):
            print("  ".join(str(x) if x is not None else "-" for x in r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
