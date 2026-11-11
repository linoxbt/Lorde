"""Step 9 (audit B18, S15): tags are never moved, labels are published without names or notes."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lorde_forge import jsonl

ROOT = Path(__file__).resolve().parent.parent


def load_publish():
    spec = importlib.util.spec_from_file_location("publish", ROOT / "forge" / "09_publish.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeApi:
    def __init__(self, tag_at=None):
        self.head = "c0"
        self.tags = {"v1.0.0": tag_at} if tag_at else {}
        self.uploads = []

    def create_repo(self, *a, **k):
        pass

    def list_repo_refs(self, repo_id):
        return SimpleNamespace(tags=[SimpleNamespace(name=n, target_commit=c) for n, c in self.tags.items()])

    def model_info(self, repo_id):
        return SimpleNamespace(sha=self.head)

    def upload_folder(self, **kw):
        self.uploads.append(kw)
        if kw.get("repo_type") in (None, "model"):
            self.head = f"c{len(self.uploads)}"

    def create_tag(self, repo_id, tag, revision=None, **kw):
        self.tags[tag] = revision

    def update_repo_settings(self, **kw):
        pass

    def create_collection(self, **kw):
        return SimpleNamespace(slug=kw["title"])

    def add_collection_item(self, *a, **k):
        pass

    def update_collection_metadata(self, *a, **k):
        pass


def world(tmp_path, monkeypatch, api):
    pub = load_publish()
    monkeypatch.setattr(pub.config, "DATA", tmp_path / "data")
    monkeypatch.setattr(pub.config, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(pub.config, "RUNS", tmp_path / "runs")
    merged = tmp_path / "runs" / "lorde-v1" / "merged"
    merged.mkdir(parents=True)
    (merged / "config.json").write_text("{}")
    jsonl.write(tmp_path / "data" / "human_sample_key.jsonl", [{"id": "h-1", "uid": "g-en-c0001-q0:gold"}])
    jsonl.write(tmp_path / "data" / "human_labels.jsonl",
                [{"uid": "h-1", "kind": "grounding", "lang": "en", "human_score": 0.9, "human_verdict": "grounded",
                  "labeler": "lino", "note": "private remark", "labeled_at": "2026-11-12T10:00:00"}])
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: api)
    return pub


def test_new_tag_points_at_the_published_commit(tmp_path, monkeypatch):
    api = FakeApi()
    pub = world(tmp_path, monkeypatch, api)
    published = {}
    real_upload = api.upload_folder

    def capture(**kw):
        if kw.get("repo_type") == "dataset":
            published.update({p.name: p.read_text() for p in Path(kw["folder_path"]).glob("*.jsonl")})
        real_upload(**kw)

    api.upload_folder = capture
    monkeypatch.setattr(sys, "argv", ["09_publish.py", "--user", "u"])
    pub.main()
    assert api.tags["v1.0.0"] == api.head
    labels = [__import__("json").loads(x) for x in published["human_labels.jsonl"].splitlines()]
    assert labels == [{"uid": "g-en-c0001-q0:gold", "kind": "grounding", "lang": "en", "human_score": 0.9,
                       "human_verdict": "grounded", "labeled_at": "2026-11-12T10:00:00"}]
    card = (tmp_path / "results" / "cards" / "model_README.md").read_text()
    top = card[: card.index("## Use it")]
    assert "license: cc-by-nc-4.0" in top and "Acceptable Use Policy" in top


def test_existing_tag_elsewhere_fails(tmp_path, monkeypatch):
    api = FakeApi(tag_at="c0")  # tagged at the current head; publishing new weights moves the head
    pub = world(tmp_path, monkeypatch, api)
    monkeypatch.setattr(sys, "argv", ["09_publish.py", "--user", "u"])
    with pytest.raises(SystemExit) as e:
        pub.main()
    assert "--tag v1.0.1" in str(e.value) and api.tags["v1.0.0"] == "c0"
    api2 = FakeApi(tag_at="old")
    pub = world(tmp_path / "b", monkeypatch, api2)
    with pytest.raises(SystemExit):
        pub.main()
    assert not api2.uploads  # refused before uploading anything
    assert pub.bump("v1.0.9") == "v1.0.10"
