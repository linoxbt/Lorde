#!/usr/bin/env python
"""Step 4: hand-label the human sample with the same rubric the gates use.

    python forge/04_label_tool.py --labeler lino                                  # http://localhost:7860
    LABEL_PASSWORD=... python forge/04_label_tool.py --share                     # public link, e.g. a phone
    LABEL_PASSWORD=... python forge/04_label_tool.py --hub <user>/lorde-work --host 0.0.0.0   # hosted

The labelling is blind: the page shows "Item 17 of 200", the gate (reward / grounding), the language and the
item text, never the item id, variant or group. The tool refuses a sample that still carries them (run
forge/04b_blind_sample.py first). It never reads data/human_sample_key.jsonl.

Reachable beyond localhost (``--hub``, ``--share`` or a non-local ``--host``) it requires a login:
LABEL_PASSWORD with the username LABEL_USER (or LABEL_NAME, default "lorde"), or LABEL_USERS="alice:pw1,bob:pw2"
for several labellers. The labeller recorded with each label is the login username (``--labeler`` locally).

Labels go to <data dir>/human_labels.jsonl, one row per (item, labeller); the newest ``labeled_at`` wins, so you
can stop and come back at any time, and several people can label the same item. The data dir is data/ by
default, or LORDE_LABEL_DATA_DIR (hosted: point it at a mounted volume, so labels survive a redeploy).

Hosted mode (``--hub``) pulls the sample at start, merges the hub's labels with the local file, and pushes after
every save. Every push is a compare-and-swap: it reads the hub's head commit, merges the labels of that commit
into the local file, and commits with ``parent_commit`` = that head, so a concurrent writer can never be
overwritten; on a conflict it re-reads, re-merges and tries again. A push that fails (network, hub down) is
never dropped: the local file is the outbox, the push is retried with backoff until it lands, and the page
shows a warning while saves are not on the hub yet. One upload runs at a time, plus a trailing upload so the
last save always reaches the hub. A file that is missing on the hub is the only error treated as "no labels".

Labellers: label from the text on the page only, and do not consult the public dataset (Hugging Face
<user>/lorde-data: pool.jsonl and test.jsonl hold the same texts with their variant) while labelling.
"""

from __future__ import annotations

import argparse
import hmac
import os
import sys
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lorde_forge import config, human, jsonl  # noqa: E402

DO_NOT_LOOK = ("**Label from this page only.** Do not look items up in the public Lorde dataset or anywhere else "
               "while labelling: the published files name each item's variant, which would de-blind your labels.")

REWARD_RUBRIC = """**Reward rubric (RewardGate).** Score the response 0.0-1.0 overall, considering
*helpfulness* (is it useful?), *honesty* (does it admit uncertainty?) and
*instruction following* (does it address every part?).
0.9-1.0 excellent · 0.7-0.8 good, minor issues · 0.5-0.6 adequate with gaps ·
0.3-0.4 poor · 0.0-0.2 largely unhelpful or wrong. **Pass = 0.7 or higher.**"""

GROUNDING_RUBRIC = """**Grounding rubric (HallucinationGate).** Is every claim in the answer supported
by the source text? Claims from outside knowledge count as unsupported even if true.
1.0 all supported · 0.7-0.9 mostly, minor phrasing · 0.4-0.6 some unsupported ·
0.0-0.3 relies on outside knowledge or contradicts the source. **Pass = 0.7 or higher.**
Then pick the verdict yourself (grounded / partially grounded / hallucinated)."""

SCORES = [f"{x / 10:.1f}" for x in range(11)]
VERDICTS = ["grounded", "partially_grounded", "hallucinated"]
NOT_BLIND = ("data/human_sample.jsonl is not blind: {why}. Labelling it would show the answer. Convert it first:\n"
             "    python forge/04b_blind_sample.py{hub}\n"
             "(writes a blind sample and the private key data/human_sample_key.jsonl)")


def data_dir() -> Path:
    """Where the sample and labels live: LORDE_LABEL_DATA_DIR (e.g. a mounted volume) or data/."""
    return Path(os.environ.get("LORDE_LABEL_DATA_DIR") or config.DATA)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def check_blind(items: list[dict]) -> str | None:
    """None if the sample is blind, else why not."""
    why = sorted({w for it in items for w in human.leaks(it)})
    return ", ".join(why) if why else None


# ----------------------------------------------------------------------------- storage and sync


class Uploader:
    """One upload at a time; a save during an upload schedules one more, so the last save always lands.

    A failed upload is never given up: it is retried with exponential backoff (capped at ``max_backoff``
    seconds) until it succeeds, and ``status()`` reports the unsynced state for the page. The labels themselves
    are safe meanwhile: they are in the local file (the outbox), which every later upload merges and pushes."""

    def __init__(self, upload: Callable[[], None], max_backoff: float = 300.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._upload = upload
        self._cv = threading.Condition()
        self._pending = False
        self._running = False
        self.max_backoff = max_backoff
        self._sleep = sleep
        self.failures = 0
        self.consecutive_failures = 0
        self.last_error: str | None = None
        self.last_ok: str | None = None
        self.unsynced_since: str | None = None

    def request(self) -> None:
        with self._cv:
            self._pending = True
            self.unsynced_since = self.unsynced_since or now()
            if self._running:
                return
            self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while True:
            with self._cv:
                if not self._pending:
                    self._running = False
                    self.unsynced_since = None
                    self._cv.notify_all()
                    return
                self._pending = False
            try:
                self._upload()
                self.consecutive_failures, self.last_error, self.last_ok = 0, None, now()
            except Exception as e:  # noqa: BLE001  (network, hub errors: retry until it lands, never crash the page)
                self.failures += 1
                self.consecutive_failures += 1
                self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                wait = min(2 ** self.consecutive_failures, self.max_backoff)
                print(f"[label] WARNING: push failed ({self.consecutive_failures} in a row): {self.last_error}; the "
                      f"labels are kept locally and the push is retried in {wait:.0f} s", flush=True)
                with self._cv:
                    self._pending = True
                self._sleep(wait)

    def status(self) -> str:
        """A warning for the page while saves are not on the hub, else ''."""
        with self._cv:
            if not (self._pending or self._running) or not self.last_error:
                return ""
            return (f"**Warning: your latest saves are not on the hub yet** (since {str(self.unsynced_since)[:19]} "
                    f"UTC; last error: {self.last_error}). They are kept on this server and retried automatically; "
                    "keep labelling, nothing is lost unless this server's disk is.")

    def flush(self, timeout: float = 60.0) -> bool:
        with self._cv:
            return self._cv.wait_for(lambda: not self._running and not self._pending, timeout)


class Store:
    """data/human_labels.jsonl; rows are keyed by (item id, labeller), newest ``labeled_at`` wins."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.RLock()
        self.on_save: Callable[[], None] | None = None

    def rows(self) -> list[dict]:
        with self.lock:
            return human.merge_labels(jsonl.read(self.path))

    def mine(self, labeler: str) -> dict[str, dict]:
        return {r["uid"]: r for r in self.rows() if (r.get("labeler") or "") == labeler}

    def add(self, row: dict) -> None:
        with self.lock, jsonl.Appender(self.path) as out:
            out.add(row)
        if self.on_save:
            self.on_save()

    def merge_in(self, rows: list[dict]) -> bool:
        """Merge foreign rows into the local file; True if the local file had rows the others lacked."""
        with self.lock:
            local = jsonl.read(self.path)
            merged = human.merge_labels(local, rows)
            jsonl.write(self.path, merged)
            theirs = {(human.label_key(r), human.label_time(r)) for r in human.merge_labels(rows)}
            return any((human.label_key(r), human.label_time(r)) not in theirs for r in merged)


LABELS_IN_REPO = "data/human_labels.jsonl"


def is_conflict(e: Exception) -> bool:
    """The hub refused a commit because its head moved (``parent_commit`` no longer the head)."""
    code = getattr(getattr(e, "response", None), "status_code", None)
    return code in (409, 412)


def is_missing(e: Exception) -> bool:
    """The labels file does not exist on the hub (yet). Anything else (network, auth, hub errors) is not that."""
    names = {c.__name__ for c in type(e).__mro__}
    code = getattr(getattr(e, "response", None), "status_code", None)
    return bool(names & {"EntryNotFoundError", "RemoteEntryNotFoundError"}) or code == 404


def hub_sync(store: Store, repo: str, api=None, download=None, attempts: int = 6,
             sleep: Callable[[float], None] = time.sleep) -> Callable[[], None]:
    """Upload function for ``Uploader``: a compare-and-swap commit of the merged labels.

    Each attempt reads the hub's head commit, merges the labels file of that commit into the local file and
    commits the merged file with ``parent_commit`` = that head. If someone else committed in between, the hub
    refuses (409/412): re-read, re-merge, retry with backoff. Other errors raise, and ``Uploader`` retries later."""
    import random

    from huggingface_hub import HfApi, hf_hub_download

    api = api or HfApi()
    download = download or hf_hub_download

    def head() -> str:
        return api.repo_info(repo, repo_type="dataset").sha

    def pull_labels(revision: str | None = None) -> list[dict]:
        try:
            return jsonl.read(download(repo, LABELS_IN_REPO, repo_type="dataset", revision=revision,
                                       force_download=True))
        except Exception as e:
            if is_missing(e):  # first run: no labels on the hub yet
                print(f"[label] no labels on {repo} yet", flush=True)
                return []
            raise  # never merge "nothing" over the hub's labels because a download failed

    def upload() -> None:
        for attempt in range(attempts):
            parent = head()
            with store.lock:
                store.merge_in(pull_labels(parent))
                data = store.path.read_bytes()
            try:
                api.upload_file(path_or_fileobj=data, path_in_repo=LABELS_IN_REPO, repo_id=repo,
                                repo_type="dataset", commit_message="human labels", parent_commit=parent)
                return
            except Exception as e:
                if not is_conflict(e):
                    raise
                wait = min(0.5 * 2 ** attempt, 10.0) * (1 + random.random())
                print(f"[label] the hub moved during the push (attempt {attempt + 1}/{attempts}); re-reading and "
                      f"merging its labels, retrying in {wait:.1f} s", flush=True)
                sleep(wait)
        raise RuntimeError(f"{repo} kept changing during {attempts} attempts; retrying later")

    upload.pull_labels = pull_labels  # type: ignore[attr-defined]
    return upload


# ----------------------------------------------------------------------------- auth


def users_from_env(env: dict[str, str], labeler: str) -> dict[str, str]:
    users = {}
    for part in (env.get("LABEL_USERS") or "").split(","):
        name, _, pw = part.strip().partition(":")
        if name and pw:
            users[name] = pw
    if env.get("LABEL_PASSWORD"):
        name = env.get("LABEL_USER") or env.get("LABEL_NAME") or (labeler if labeler != "anon" else "lorde")
        users[name] = env["LABEL_PASSWORD"]
    return users


def make_auth(users: dict[str, str], max_fails: int = 10, window_s: float = 600, lockout_s: float = 300,
              delay_s: float = 1.0) -> Callable[[str, str], bool]:
    """Constant-time check, a delay after each failure, and a per-username lockout after repeated failures."""
    fails: dict[str, deque] = defaultdict(deque)
    locked: dict[str, float] = {}
    lock = threading.Lock()

    def auth(username: str, password: str) -> bool:
        t = time.time()
        with lock:
            if locked.get(username, 0) > t:
                return False
        want = users.get(username)
        ok = want is not None and hmac.compare_digest(want.encode(), (password or "").encode())
        if ok:
            return True
        with lock:
            q = fails[username]
            q.append(t)
            while q and q[0] < t - window_s:
                q.popleft()
            if len(q) >= max_fails:
                locked[username] = t + lockout_s
                q.clear()
                print(f"[label] login locked for {lockout_s:.0f} s after {max_fails} failures", flush=True)
        time.sleep(delay_s)
        return False

    return auth


# ----------------------------------------------------------------------------- the page


class Labeler:
    """View and save logic, independent of Gradio so it can be tested. Items are looked up server-side by
    position; nothing that identifies an item (its id, variant or group) leaves this object."""

    def __init__(self, items: list[dict], store: Store, sync_status: Callable[[], str] | None = None) -> None:
        why = check_blind(items)
        if why:
            raise ValueError(why)
        self.items = items
        self.store = store
        self.sync_status = sync_status or (lambda: "")

    def next_index(self, labeler: str, start: int = 0) -> int:
        done = self.store.mine(labeler)
        for k in range(len(self.items)):
            i = (start + k) % len(self.items)
            if self.items[i]["uid"] not in done:
                return i
        return -1

    def render(self, i: int, labeler: str) -> dict:
        n_done = len(set(self.store.mine(labeler)) & {it["uid"] for it in self.items})
        progress = f"you have labelled {n_done} of {len(self.items)}"
        warn = self.sync_status()
        warn = f"\n\n{warn}" if warn else ""
        if i < 0:
            return {"state": {"i": -1, "shown_at": now()}, "head": f"**All done** · {progress}.{warn}", "rubric": "",
                    "body": "", "grounding": False}
        it = self.items[i]
        head = f"**Item {i + 1} of {len(self.items)}** · {it['kind']} · {it['lang']} · {progress}{warn}"
        if it["kind"] == "reward":
            body = f"### Instruction\n{it['instruction']}\n\n### Response\n{it['response']}"
            rubric = REWARD_RUBRIC
        else:
            body = (f"### Source text\n{it['source_text']}\n\n### Question\n{it['question']}"
                    f"\n\n### Answer\n{it['answer']}")
            rubric = GROUNDING_RUBRIC
        return {"state": {"i": i, "shown_at": now()}, "head": head, "rubric": rubric, "body": body,
                "grounding": it["kind"] == "grounding"}

    def open(self, labeler: str, start: int = 0) -> dict:
        return self.render(self.next_index(labeler, start), labeler)

    def save(self, state: dict, score: str | None, verdict: str | None, note: str, overwrite: bool,
             labeler: str) -> tuple[dict | None, str]:
        """(next view or None to keep the form, status message)."""
        i = int((state or {}).get("i", -1))
        if not labeler:
            return None, "No labeller identity: log in again."
        if not 0 <= i < len(self.items):
            return self.open(labeler), ""
        it = self.items[i]
        if score in (None, ""):
            return None, "Pick a score first (nothing was saved)."
        if it["kind"] == "grounding" and verdict not in VERDICTS:
            return None, "Pick a verdict first (nothing was saved)."
        prev = self.store.mine(labeler).get(it["uid"])
        if prev and human.label_time(prev) > str(state.get("shown_at") or "") and not overwrite:
            return None, (f"You already labelled this item at {human.label_time(prev)[:19]} (another tab?). "
                          "Nothing was saved: tick *Replace my earlier label* to overwrite it, or Skip.")
        self.store.add({"uid": it["uid"], "kind": it["kind"], "lang": it["lang"],
                        "human_score": round(float(score), 1),
                        "human_verdict": verdict if it["kind"] == "grounding" else None,
                        "labeler": labeler, "note": (note or "").strip() or None, "labeled_at": now()})
        return self.open(labeler, i + 1), f"Saved item {i + 1}."

    def skip(self, state: dict, labeler: str) -> dict:
        return self.open(labeler, int((state or {}).get("i", -1)) + 1)


def build_app(lab: Labeler, labeler_of: Callable[[object], str], title: str = "Lorde · human labels"):
    import gradio as gr

    def outs(v: dict, status: str = ""):
        return (v["state"], v["head"], v["rubric"], v["body"], gr.update(value=None),
                gr.update(value=None, visible=v["grounding"]), gr.update(value=""), gr.update(value=False), status)

    def on_load(request: gr.Request):
        return outs(lab.open(labeler_of(request)))

    def on_save(state, score, verdict, note, overwrite, request: gr.Request):
        v, status = lab.save(state, score, verdict, note, overwrite, labeler_of(request))
        if v is None:
            return (state, gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
                    gr.update(), status)
        return outs(v, status)

    def on_skip(state, request: gr.Request):
        return outs(lab.skip(state, labeler_of(request)))

    for fn in (on_load, on_save, on_skip):  # string annotations (PEP 563) would hide gr.Request from Gradio
        fn.__annotations__["request"] = gr.Request

    with gr.Blocks(title=title) as demo:
        gr.Markdown("# Lorde · human labels\nScore each item with the gate's own rubric. Every item needs an "
                    "explicit score (and, for grounding, a verdict); nothing is filled in for you.\n\n" + DO_NOT_LOOK)
        st = gr.State({"i": -1, "shown_at": ""})
        head = gr.Markdown()
        rubric = gr.Markdown()
        body = gr.Markdown()
        with gr.Row():
            score = gr.Radio(SCORES, value=None, label="Score (pass = 0.7 or higher)")
            verdict = gr.Radio(VERDICTS, value=None, label="Verdict", visible=False)
        note = gr.Textbox(label="Note (optional)")
        overwrite = gr.Checkbox(value=False, label="Replace my earlier label for this item")
        status = gr.Markdown()
        with gr.Row():
            save_btn = gr.Button("Save and next", variant="primary")
            skip_btn = gr.Button("Skip")
        o = [st, head, rubric, body, score, verdict, note, overwrite, status]
        demo.load(on_load, inputs=None, outputs=o)
        save_btn.click(on_save, inputs=[st, score, verdict, note, overwrite], outputs=o)
        skip_btn.click(on_skip, inputs=[st], outputs=o)
    return demo


def exposed(args) -> bool:
    return bool(args.hub or args.share or (args.host not in (None, "127.0.0.1", "localhost")))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", default=str(data_dir() / "human_sample.jsonl"))
    ap.add_argument("--out", default=str(data_dir() / "human_labels.jsonl"),
                    help="labels file (the outbox in hosted mode); default <LORDE_LABEL_DATA_DIR or data/>")
    ap.add_argument("--labeler", default=os.environ.get("LABEL_NAME", "anon"),
                    help="labeller name when no login is required (local use)")
    ap.add_argument("--share", action="store_true")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 7860)))
    ap.add_argument("--host", default=None)
    ap.add_argument("--hub", default=os.environ.get("LABEL_HUB_REPO"), help="HF dataset repo to sync with")
    args = ap.parse_args()

    users = users_from_env(dict(os.environ), args.labeler)
    if exposed(args) and not users:
        sys.exit("[label] refusing to start: this page would be reachable beyond localhost without a login. "
                 "Set LABEL_PASSWORD (and LABEL_USER or LABEL_NAME), or LABEL_USERS=name:password,...")

    out_dir = Path(args.out).parent
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".write_test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        sys.exit(f"[label] cannot write labels to {out_dir} ({e}). Set LORDE_LABEL_DATA_DIR to a writable directory "
                 "(hosted: a mounted volume the container user can write).")
    store = Store(Path(args.out))
    uploader = None
    if args.hub:
        from huggingface_hub import hf_hub_download

        def pull_sample() -> list[dict] | None:
            try:
                return jsonl.read(hf_hub_download(args.hub, "data/human_sample.jsonl", repo_type="dataset",
                                                  force_download=True))
            except Exception as e:  # noqa: BLE001
                print(f"[label] human_sample.jsonl not pulled from {args.hub}: {e}", flush=True)
                return None

        while True:  # hosted before step 3 has run, or before the sample was blinded: wait, then re-check
            items = pull_sample()
            why = check_blind(items) if items else "no sample yet"
            if items and not why:
                break
            if items:
                print("[label] " + NOT_BLIND.format(why=why, hub=f" --hub {args.hub}") + "\n[label] labelling is "
                      "disabled; re-checking the hub every 2 minutes.", flush=True)
            time.sleep(120)
        jsonl.write(args.sample, items)
        upload = hub_sync(store, args.hub)
        uploader = Uploader(upload)
        if store.merge_in(upload.pull_labels()):  # type: ignore[attr-defined]
            uploader.request()  # the local file had labels the hub lacked
        store.on_save = uploader.request
    else:
        items = jsonl.read(args.sample)
        if not items:
            sys.exit("no human sample: run forge/03_make_splits.py first")
        why = check_blind(items)
        if why:
            sys.exit(NOT_BLIND.format(why=why, hub=""))

    lab = Labeler(items, store, sync_status=uploader.status if uploader else None)
    if users:
        def labeler_of(request) -> str:
            return getattr(request, "username", None) or ""
    else:
        def labeler_of(request) -> str:
            return args.labeler

    demo = build_app(lab, labeler_of)
    demo.launch(share=args.share, server_port=args.port, server_name=args.host,
                auth=make_auth(users) if users else None, auth_message="Lorde labelling: log in to continue")


if __name__ == "__main__":
    main()
