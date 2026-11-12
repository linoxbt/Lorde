"""Model registry (spec FR-11, FR-12): candidate -> active -> retired, in the model_versions table.

    python -m gateway.models_cli promote <hf_repo> <revision> [--name NAME] [--bench judge_eval.json]
                                         [--fingerprints template_fingerprints.json] [--force REASON]
    python -m gateway.models_cli list

``promote`` retires the active version and activates the given one in one transaction (registering it
first if it is new), and records model.promote in the audit log. Gateways pick the active version up
within a minute (LORDE_MODEL_REGISTRY=1, the default); LORDE_MODEL_REPO/REVISION stay the fallback when
no version is active. Point Lorde Serve at the same weights before promoting.

Audit 2 (M5):

- Promotion is gated on Lorde Bench (spec 9.2): the version's bench result (``--bench``, the step-6 report
  ``judge_eval.json``, or the one stored with the version) must say ``promotable: true``. Otherwise ``promote``
  refuses (exit 1, listing the failing blocking criteria) unless ``--force "<reason>"`` is given; the reason and
  the failing criteria are written to the audit log.
- Template fingerprints are stored with the version when it is registered: from ``--fingerprints`` (the
  ``template_fingerprints.json`` that forge/09_publish.py ships in the model repo) or, by default, that file
  downloaded from the Hub at the exact revision. The Gateway reports the stored fingerprints of the active
  version (and whether today's CuratorKIT still renders the same templates) instead of recomputing a baseline
  from whatever CuratorKIT is installed. A version whose fingerprints cannot be found is refused (``--force``
  stores the installed CuratorKIT's, marked as such).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


class NotPromotable(RuntimeError):
    pass


def bench_verdict(bench: dict | None) -> tuple[bool, list[str]]:
    """(promotable, failing blocking criteria) of a Lorde Bench / step-6 report. No report: not promotable."""
    if not bench:
        return False, ["no Lorde Bench result stored for this version (pass --bench judge_eval.json)"]
    acc = bench.get("acceptance")
    prom = bench.get("promotable")
    if prom is None and isinstance(acc, dict):
        prom = acc.get("promotable")
    failing = [f"{c.get('criterion')}: {c.get('value')}" + (f" ({c['reason']})" if c.get("reason") else "")
               for c in (acc if isinstance(acc, list) else []) if c.get("blocking") and c.get("met") is not True]
    if prom is not True and not failing:
        failing = ["the bench result does not say promotable: true"]
    return prom is True and not failing, failing


def fetch_fingerprints(repo: str, revision: str) -> dict | None:
    """template_fingerprints.json from the model repo at ``revision`` (public; None if it is not there)."""
    try:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo, "template_fingerprints.json", revision=revision, token=False)
    except Exception:  # noqa: BLE001  (offline, private, or not published with this file)
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def installed_fingerprints() -> dict:
    from lorde_forge import contract

    return {**contract.fingerprints(), "source": "installed CuratorKIT at registration (forced)"}


def promote(url: str, repo: str, revision: str, name: str | None = None, bench: dict | None = None,
            actor: str = "cli", fingerprints: dict | None = None, force: str | None = None) -> dict:
    with psycopg.connect(url) as c, c.transaction():
        c.execute("lock table model_versions in share row exclusive mode")  # one promotion at a time
        row = c.execute("select id, bench, template_fingerprints from model_versions where hf_repo = %s and "
                        "revision = %s order by created_at desc limit 1", (repo, revision)).fetchone()
        bench = bench or (row[1] if row else None)
        ok, failing = bench_verdict(bench)
        if not ok and not force:
            raise NotPromotable(f"{repo}@{revision} is not promotable by Lorde Bench:\n  - " + "\n  - ".join(failing)
                                + "\nFix the model, or pass --force \"<reason>\" (recorded in the audit log).")
        fps = fingerprints or (row[2] if row else None) or fetch_fingerprints(repo, revision)
        if not fps:
            if not force:
                raise NotPromotable(f"no template_fingerprints.json for {repo}@{revision}: pass --fingerprints FILE "
                                    "(the file forge/09_publish.py ships with the model)")
            fps = installed_fingerprints()
        prev = c.execute("update model_versions set state = 'retired' where state = 'active' "
                         "returning hf_repo, revision").fetchall()
        if row:
            c.execute("update model_versions set state = 'active', promoted_at = now(), name = coalesce(%s, name), "
                      "bench = %s, template_fingerprints = %s where id = %s",
                      (name, Jsonb(bench) if bench else None, Jsonb(fps), row[0]))
            vid = row[0]
        else:
            vid = c.execute("insert into model_versions (name, hf_repo, revision, state, bench, template_fingerprints, "
                            "promoted_at) values (%s, %s, %s, 'active', %s, %s, now()) returning id",
                            (name, repo, revision, Jsonb(bench) if bench else None, Jsonb(fps))).fetchone()[0]
        detail: dict[str, Any] = {"retired": [f"{r[0]}@{r[1]}" for r in prev], "promotable": ok}
        if not ok:
            detail.update({"forced": force, "failing": failing})
        c.execute("insert into audit_log (org_id, actor, action, target, detail) values "
                  "(null, %s, 'model.promote', %s, %s)", (actor, f"{repo}@{revision}", Jsonb(detail)))
    return {"id": str(vid), "active": f"{repo}@{revision}", "retired": detail["retired"], "promotable": ok,
            **({"forced": force} if not ok else {})}


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
    p.add_argument("--bench", help="Lorde Bench / step-6 report JSON (judge_eval.json) to store with the version")
    p.add_argument("--fingerprints", help="the model's template_fingerprints.json (default: from the Hub)")
    p.add_argument("--force", metavar="REASON", help="promote although Lorde Bench does not say promotable")
    sub.add_parser("list")
    a = ap.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    if a.cmd == "promote":
        if a.force is not None and len(a.force.strip()) < 10:
            print("--force needs a reason of at least 10 characters (it goes into the audit log)", file=sys.stderr)
            return 2
        bench = json.loads(open(a.bench, encoding="utf-8").read()) if a.bench else None
        fps = json.loads(open(a.fingerprints, encoding="utf-8").read()) if a.fingerprints else None
        try:
            out = promote(url, a.repo, a.revision, a.name, bench, fingerprints=fps, force=a.force)
        except NotPromotable as e:
            print(str(e), file=sys.stderr)
            return 1
        if not out["promotable"]:
            print(f"WARNING: forced promotion of a model Lorde Bench does not pass ({a.force})", file=sys.stderr)
        print(json.dumps(out))
    else:
        for r in list_versions(url):
            print("  ".join(str(x) if x is not None else "-" for x in r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
