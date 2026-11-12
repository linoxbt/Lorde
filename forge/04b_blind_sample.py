#!/usr/bin/env python
"""Step 4b (one-off): convert an existing human_sample.jsonl into the blind form the label tool needs.

    python forge/04b_blind_sample.py                               # local data/human_sample.jsonl
    python forge/04b_blind_sample.py --hub <user>/lorde-work       # the hub copy (needs HF_TOKEN, write)
    python forge/04b_blind_sample.py --hub <user>/lorde-work --dry-run

Samples written by step 3 before the fix carry the real item uids (``g-en-c0050-q0:fabricated_numbers``),
``variant`` and ``group``: they tell the labeller the answer. This rewrites the sample with opaque ids
(``h-<sha1(salt + uid)[:10]>``), kind, lang and item text only, in hash order, and writes the private key
data/human_sample_key.jsonl (opaque id -> uid, variant, group) that step 6 uses to join the labels. The label
tool never reads the key. Existing labels keyed by the original uid stay valid. Re-running on a blind
sample does nothing.

With ``--hub``, the key is read from and written to ``--key-repo`` (default LORDE_LABEL_KEY_REPO, else the --hub
repo): a private repo the label service's token cannot read, so the hosted labeller cannot look items up.

Known tell: fabricated-number items whose answer had no number end with " (<year>; <pct>%)" (a generation
artefact of lorde_forge.variants.fabricate_numbers; 14 of the 200 published sample items). The text is left as it
is, because the human label must be about the exact text the judges saw; the conversion reports how many items
carry it, and step 3 leaves such items out of future samples.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, human, jsonl  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", default=str(config.DATA / "human_sample.jsonl"))
    ap.add_argument("--key", default=str(config.DATA / "human_sample_key.jsonl"))
    ap.add_argument("--hub", default=None, help="HF dataset repo holding data/human_sample.jsonl (e.g. the work repo)")
    ap.add_argument("--key-repo", default=os.environ.get("LORDE_LABEL_KEY_REPO"),
                    help="HF dataset repo for data/human_sample_key.jsonl (default: LORDE_LABEL_KEY_REPO, else --hub)")
    ap.add_argument("--salt", default=None, help="default: random")
    ap.add_argument("--dry-run", action="store_true", help="convert and report, write nothing")
    args = ap.parse_args()

    sample_path, key_path = Path(args.sample), Path(args.key)
    key_rows: list[dict] = []
    if args.hub:
        from huggingface_hub import hf_hub_download

        rows = jsonl.read(hf_hub_download(args.hub, "data/human_sample.jsonl", repo_type="dataset",
                                          force_download=True))
        try:
            key_rows = jsonl.read(hf_hub_download(args.key_repo or args.hub, "data/human_sample_key.jsonl",
                                                  repo_type="dataset", force_download=True))
        except Exception:  # noqa: BLE001  (no key yet: the usual case)
            key_rows = []
    else:
        rows = jsonl.read(sample_path)
        key_rows = jsonl.read(key_path)
    if not rows:
        sys.exit("empty human sample: nothing to convert")
    if human.is_blind(rows):
        print(f"[blind] the sample is already blind ({len(rows)} items); nothing to do")
        return

    sample, key = human.blind_keep(rows, args.salt, key_rows)
    assert human.is_blind(sample), "conversion left identifying fields"
    print(f"[blind] {len(sample)} items -> opaque ids; key has {len(key)} entries")
    tells = sum(bool(human.text_tells(s)) for s in sample)
    if tells:
        print(f"[blind] note: {tells} items end with the fabricated-figure suffix (a generation artefact); their text "
              "is kept as the judges saw it. Labellers should judge them on the source like any other item.")
    if args.dry_run:
        print("[blind] dry run: first item would be", {k: sample[0][k] for k in ("uid", "kind", "lang")})
        return

    if args.hub:
        from huggingface_hub import CommitOperationAdd, HfApi

        def blob(rs: list[dict]) -> bytes:
            import json

            return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs).encode()

        api = HfApi()
        key_repo = args.key_repo or args.hub
        if key_repo == args.hub:
            print(f"[blind] WARNING: the key goes to {args.hub}, the repo the label service syncs with; set "
                  "LORDE_LABEL_KEY_REPO (or --key-repo) to a private repo its token cannot read")
            api.create_commit(
                repo_id=args.hub, repo_type="dataset",
                operations=[CommitOperationAdd("data/human_sample_key.jsonl", blob(key)),
                            CommitOperationAdd("data/human_sample.jsonl", blob(sample))],
                commit_message="Blind the human sample (opaque ids; private key in data/human_sample_key.jsonl)")
        else:  # key first: never a blind sample without its key
            api.create_repo(key_repo, repo_type="dataset", private=True, exist_ok=True)
            api.create_commit(repo_id=key_repo, repo_type="dataset",
                              operations=[CommitOperationAdd("data/human_sample_key.jsonl", blob(key))],
                              commit_message="Human sample key (private)")
            api.create_commit(repo_id=args.hub, repo_type="dataset",
                              operations=[CommitOperationAdd("data/human_sample.jsonl", blob(sample))],
                              commit_message="Blind the human sample (opaque ids; key kept in a separate repo)")
        print(f"[blind] pushed data/human_sample.jsonl to {args.hub} and data/human_sample_key.jsonl to {key_repo}")
    # Key first: a crash between the two writes must never leave a blind sample without its key.
    jsonl.write(key_path, key)
    jsonl.write(sample_path, sample)
    print(f"[blind] wrote {sample_path} and {key_path}")


if __name__ == "__main__":
    main()
