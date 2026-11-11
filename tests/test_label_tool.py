"""Blind labelling (audit B1, B17, S12): nothing that identifies an item reaches the labeller."""

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from lorde_forge import human, jsonl

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ("gold", "fabricated_numbers", "closed_book", "truncated", "student", "off_topic")


def load_tool():
    spec = importlib.util.spec_from_file_location("label_tool", ROOT / "forge" / "04_label_tool.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def legacy_sample(n=12):
    """What step 3 wrote before the fix: real uids that embed the variant, plus variant and group."""
    rows = []
    for i in range(n):
        v = VARIANTS[i % len(VARIANTS)]
        if i % 2:
            rows.append({"uid": f"g-en-c{i:04d}-q0:{v}", "kind": "grounding", "lang": "en", "variant": v,
                         "group": f"g-en-c{i:04d}", "source_text": f"Source {i}.", "question": f"Q{i}?",
                         "answer": f"A{i}."})
        else:
            rows.append({"uid": f"r-fr-{i:04d}:{v}", "kind": "reward", "lang": "fr", "variant": v,
                         "group": f"r-fr-{i:04d}", "instruction": f"Instruction {i}", "response": f"Response {i}"})
    return rows


def secrets_of(rows):
    out = set()
    for r in rows:
        out |= {r["uid"], r["group"], r["uid"].split(":")[0]}
    return out


def test_blind_conversion_and_key_round_trip():
    rows = legacy_sample()
    assert not human.is_blind(rows)
    sample, key = human.blind(rows, salt="s")
    assert human.is_blind(sample)
    blob = json.dumps(sample)
    assert not any(s in blob for s in secrets_of(rows))
    assert not any(f'"{v}"' in blob or f":{v}" in blob for v in VARIANTS)
    assert all(set(s) <= {"uid", "kind", "lang", *human.ITEM_FIELDS} for s in sample)
    assert [s["uid"] for s in sample] == sorted(s["uid"] for s in sample)  # hash order, not stratum order
    assert {k["id"]: k["uid"] for k in key} == {human.opaque_id(r["uid"], "s"): r["uid"] for r in rows}
    # ids are stable when the key already exists, whatever the salt
    sample2, _ = human.blind(rows, salt="other", existing_key=key)
    assert [s["uid"] for s in sample2] == [s["uid"] for s in sample]


def test_04b_converts_a_legacy_sample_and_eval_join_maps_back(tmp_path):
    rows = legacy_sample()
    sp, kp = tmp_path / "human_sample.jsonl", tmp_path / "human_sample_key.jsonl"
    jsonl.write(sp, rows)
    env = {**os.environ, "LORDE_ROOT": str(tmp_path)}
    cmd = [sys.executable, str(ROOT / "forge" / "04b_blind_sample.py"), "--sample", str(sp), "--key", str(kp)]
    p = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    sample, key = jsonl.read(sp), jsonl.read(kp)
    assert human.is_blind(sample) and len(key) == len(rows)
    p2 = subprocess.run(cmd, env=env, capture_output=True, text=True)  # idempotent
    assert p2.returncode == 0 and "already blind" in p2.stdout and jsonl.read(sp) == sample

    # labels by opaque id (new tool), plus one legacy label keyed by the original uid, plus two labellers
    labels = [{"uid": s["uid"], "kind": s["kind"], "lang": s["lang"], "human_score": 0.8,
               "human_verdict": "grounded" if s["kind"] == "grounding" else None, "labeler": "a",
               "labeled_at": f"2026-11-12T10:00:{i:02d}"} for i, s in enumerate(sample)]
    labels.append({**labels[0], "labeler": "b", "human_score": 0.2, "labeled_at": "2026-11-12T11:00:00"})
    labels.append({"uid": rows[0]["uid"], "kind": rows[0]["kind"], "lang": "fr", "human_score": 0.5,
                   "human_verdict": None, "labeler": "old", "ts": "2026-11-10T00:00:00"})
    labels.append({"uid": "h-unknown000", "human_score": 1.0, "labeler": "a", "labeled_at": "x"})
    jsonl.write(tmp_path / "labels.jsonl", labels)
    hl = human.load_labels(tmp_path / "labels.jsonl", kp)
    assert set(hl) == {r["uid"] for r in rows}  # all mapped back to test uids; unknown id dropped
    first = {k["id"]: k["uid"] for k in key}[sample[0]["uid"]]
    assert hl[first]["n_labelers"] == 2 and hl[first]["human_score"] == 0.5  # mean of 0.8 and 0.2
    pub = human.public_labels(labels, kp)
    assert all("labeler" not in r and "note" not in r for r in pub)
    assert {r["uid"] for r in pub} >= {r["uid"] for r in rows}


def test_merge_keeps_newest_per_item_and_labeller():
    a = {"uid": "h-1", "labeler": "x", "human_score": 0.1, "labeled_at": "2026-11-12T10:00:00"}
    b = {**a, "human_score": 0.9, "labeled_at": "2026-11-12T11:00:00"}
    c = {**a, "labeler": "y", "human_score": 0.5}
    merged = human.merge_labels([b, c], [a])
    assert {(r["labeler"], r["human_score"]) for r in merged} == {("x", 0.9), ("y", 0.5)}


def test_label_tool_refuses_unblinded_sample(tmp_path):
    tool = load_tool()
    with pytest.raises(ValueError):
        tool.Labeler(legacy_sample(), tool.Store(tmp_path / "l.jsonl"))
    jsonl.write(tmp_path / "s.jsonl", legacy_sample())
    p = subprocess.run([sys.executable, str(ROOT / "forge" / "04_label_tool.py"), "--sample", str(tmp_path / "s.jsonl"),
                        "--out", str(tmp_path / "l.jsonl")], capture_output=True, text=True,
                       env={**os.environ, "LORDE_ROOT": str(tmp_path)})
    assert p.returncode != 0 and "04b_blind_sample.py" in p.stderr


@pytest.mark.parametrize("flags", [["--host", "0.0.0.0"], ["--share"], ["--hub", "someone/repo"]])
def test_hosted_mode_refuses_to_start_without_password(tmp_path, flags):
    env = {k: v for k, v in os.environ.items() if not k.startswith("LABEL_")}
    p = subprocess.run([sys.executable, str(ROOT / "forge" / "04_label_tool.py"), *flags], capture_output=True,
                       text=True, env={**env, "LORDE_ROOT": str(tmp_path)})
    assert p.returncode != 0 and "LABEL_PASSWORD" in p.stderr


def test_view_and_gradio_config_leak_nothing(tmp_path):
    pytest.importorskip("gradio")
    tool = load_tool()
    rows = legacy_sample()
    sample, key = human.blind(rows, salt="s")
    lab = tool.Labeler(sample, tool.Store(tmp_path / "labels.jsonl"))
    forbidden = (secrets_of(rows) | {s["uid"] for s in sample} | {f":{v}" for v in VARIANTS}
                 | {f'"{v}"' for v in VARIANTS} | {'"variant": "gold"', '"group"'})

    views = []
    v = lab.open("tester")
    for _ in range(len(sample)):
        views.append(v)
        v = lab.skip(v["state"], "tester")
    blob = json.dumps(views)
    assert not [s for s in forbidden if s in blob]
    assert views[0]["head"].startswith("**Item 1 of 12**")
    assert {"reward", "grounding"} <= {w["head"].split(" · ")[1] for w in views}

    demo = tool.build_app(lab, lambda request: "tester")
    cfg = json.dumps(demo.get_config_file(), default=str)
    assert not [s for s in forbidden if s in cfg]
    # the Gradio handlers return only the view (call them as Gradio would, with a request)
    fns = {f.fn.__name__: f.fn for f in demo.fns.values()}
    out = fns["on_load"](request=type("R", (), {"username": "tester"})())
    assert not [s for s in forbidden if s in json.dumps(out, default=str)]
    out = fns["on_save"](views[0]["state"], None, None, "", False, request=type("R", (), {"username": "tester"})())
    assert "Pick a score" in out[-1] and not jsonl.read(tmp_path / "labels.jsonl")


def test_save_requires_explicit_choices_and_guards_other_tabs(tmp_path):
    tool = load_tool()
    sample, _ = human.blind(legacy_sample(4), salt="s")
    store = tool.Store(tmp_path / "labels.jsonl")
    lab = tool.Labeler(sample, store)
    g = next(i for i, s in enumerate(sample) if s["kind"] == "grounding")
    tab1 = lab.render(g, "ana")
    tab2 = lab.render(g, "ana")

    v, msg = lab.save(tab1["state"], None, None, "", False, "ana")
    assert v is None and "score" in msg
    v, msg = lab.save(tab1["state"], "0.8", None, "", False, "ana")
    assert v is None and "verdict" in msg  # no verdict auto-filled from the score
    assert not jsonl.read(store.path)
    v, msg = lab.save(tab1["state"], "0.8", "grounded", " ok ", False, "ana")
    assert v is not None and msg.startswith("Saved")
    row = jsonl.read(store.path)[0]
    assert row["uid"] == sample[g]["uid"] and row["labeler"] == "ana" and row["human_score"] == 0.8
    assert row["human_verdict"] == "grounded" and row["note"] == "ok" and row["labeled_at"]

    # the second tab still shows the same item: refused unless the labeller confirms the overwrite
    time.sleep(0.001)
    v, msg = lab.save(tab2["state"], "0.2", "hallucinated", "", False, "ana")
    assert v is None and "already labelled" in msg and len(jsonl.read(store.path)) == 1
    v, msg = lab.save(tab2["state"], "0.2", "hallucinated", "", True, "ana")
    assert v is not None and store.mine("ana")[sample[g]["uid"]]["human_score"] == 0.2
    # another labeller labels the same item independently
    v, msg = lab.save(lab.render(g, "ben")["state"], "0.9", "grounded", "", False, "ben")
    assert v is not None and len(store.rows()) == 2
    assert lab.next_index("ana", g) != g


def test_uploader_always_uploads_the_last_save(tmp_path):
    tool = load_tool()
    state = {"value": 0}
    uploaded = []
    started = threading.Event()

    def upload():
        started.set()
        snapshot = state["value"]
        time.sleep(0.2)
        uploaded.append(snapshot)

    up = tool.Uploader(upload)
    state["value"] = 1
    up.request()
    started.wait(2)
    for n in (2, 3, 4):  # saves while the first upload runs
        state["value"] = n
        up.request()
    assert up.flush(5)
    assert uploaded[-1] == 4 and len(uploaded) <= 3


def test_hub_sync_merges_before_push(tmp_path):
    tool = load_tool()
    store = tool.Store(tmp_path / "labels.jsonl")
    store.add({"uid": "h-1", "labeler": "a", "human_score": 0.3, "labeled_at": "2026-11-12T10:00:00"})
    hub_file = tmp_path / "hub.jsonl"
    jsonl.write(hub_file, [{"uid": "h-1", "labeler": "a", "human_score": 0.9, "labeled_at": "2026-11-12T12:00:00"},
                           {"uid": "h-2", "labeler": "b", "human_score": 0.5, "labeled_at": "2026-11-12T09:00:00"}])
    pushed = {}

    class Api:
        def upload_file(self, path_or_fileobj, **kw):
            pushed["data"] = path_or_fileobj

    upload = tool.hub_sync(store, "x/y", api=Api(), download=lambda *a, **k: str(hub_file))
    upload()
    rows = {(r["uid"], r["labeler"]): r["human_score"] for r in map(json.loads, pushed["data"].decode().splitlines())}
    assert rows == {("h-1", "a"): 0.9, ("h-2", "b"): 0.5}


def test_auth_from_env_and_lockout():
    tool = load_tool()
    users = tool.users_from_env({"LABEL_PASSWORD": "pw", "LABEL_NAME": "lino", "LABEL_USERS": "ana:x1"}, "anon")
    assert users == {"lino": "pw", "ana": "x1"}
    auth = tool.make_auth(users, max_fails=3, delay_s=0)
    assert auth("lino", "pw") and not auth("lino", "nope") and not auth("eve", "pw")
    for _ in range(3):
        auth("ana", "bad")
    assert not auth("ana", "x1")  # locked out
    assert auth("lino", "pw")
