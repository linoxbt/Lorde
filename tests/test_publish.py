"""Step 9 (audit B18, S15, N3): the tag pins the weights and is never moved; labels are published without names
or notes; a card-only re-publish (private, then --public) still publishes the data repo, collections and
visibility."""

import hashlib
import importlib.util
import json
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


def hub_file(path: str, data: bytes):
    """A RepoFile as list_repo_tree returns it: big/binary files in LFS (sha256 oid), the rest as git blobs."""
    if path.endswith((".safetensors", ".gguf")) or path == "tokenizer.json":
        return SimpleNamespace(path=path, size=len(data), blob_id="pointer-" + path,
                               lfs=SimpleNamespace(sha256=hashlib.sha256(data).hexdigest(), size=len(data)))
    return SimpleNamespace(path=path, size=len(data), lfs=None,
                           blob_id=hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest())


class FakeApi:
    """A model repo with real commits: upload_folder adds a commit holding the folder's files."""

    def __init__(self):
        self.commits = {"c0": {}}  # commit -> {path: bytes}
        self.head = "c0"
        self.tags = {}
        self.uploads = []
        self.settings = []
        self.collections = []

    def create_repo(self, *a, **k):
        pass

    def list_repo_refs(self, repo_id):
        return SimpleNamespace(tags=[SimpleNamespace(name=n, target_commit=c) for n, c in self.tags.items()])

    def list_repo_tree(self, repo_id, revision=None, recursive=False, **kw):
        commit = self.head if revision in (None, "main") else self.tags.get(revision, revision)
        files = [hub_file(p, d) for p, d in self.commits[commit].items()]
        return files + [SimpleNamespace(path="subdir", tree_id="t")]  # a folder: ignored

    def model_info(self, repo_id):
        return SimpleNamespace(sha=self.head)

    def upload_folder(self, **kw):
        self.uploads.append(kw)
        if kw.get("repo_type") in (None, "model"):
            folder = Path(kw["folder_path"])
            files = dict(self.commits[self.head])
            files.update({f.relative_to(folder).as_posix(): f.read_bytes() for f in folder.rglob("*") if f.is_file()})
            self.head = f"c{len(self.commits)}"
            self.commits[self.head] = files

    def create_tag(self, repo_id, tag, revision=None, **kw):
        assert tag not in self.tags, "tags are never moved"
        self.tags[tag] = revision

    def update_repo_settings(self, **kw):
        self.settings.append(kw)

    def create_collection(self, **kw):
        return SimpleNamespace(slug=kw["title"])

    def add_collection_item(self, slug, **k):
        self.collections.append((slug, k["item_id"]))

    def update_collection_metadata(self, slug, private=None):
        self.settings.append({"collection": slug, "private": private})

    def model_uploads(self):
        return [u for u in self.uploads if u.get("repo_type") in (None, "model")]

    def data_uploads(self):
        return [u for u in self.uploads if u.get("repo_type") == "dataset"]


def world(tmp_path, monkeypatch, api):
    pub = load_publish()
    monkeypatch.setattr(pub.config, "DATA", tmp_path / "data")
    monkeypatch.setattr(pub.config, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(pub.config, "RUNS", tmp_path / "runs")
    merged = tmp_path / "runs" / "lorde-v1" / "merged"
    merged.mkdir(parents=True, exist_ok=True)
    (merged / "config.json").write_text('{"model_type": "cohere2"}')
    (merged / "model.safetensors").write_bytes(b"weights v1")
    (merged / "tokenizer.json").write_text('{"vocab": 1}')
    jsonl.write(tmp_path / "data" / "human_sample_key.jsonl", [{"id": "h-1", "uid": "g-en-c0001-q0:gold"}])
    jsonl.write(tmp_path / "data" / "human_labels.jsonl",
                [{"uid": "h-1", "kind": "grounding", "lang": "en", "human_score": 0.9, "human_verdict": "grounded",
                  "labeler": "lino", "note": "private remark", "labeled_at": "2026-11-12T10:00:00"}])
    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: api)
    return pub


def run(pub, monkeypatch, *extra):
    monkeypatch.setattr(sys, "argv", ["09_publish.py", "--user", "u", *extra])
    pub.main()


def test_first_publish_creates_the_tag_at_the_published_commit(tmp_path, monkeypatch):
    api = FakeApi()
    pub = world(tmp_path, monkeypatch, api)
    published = {}
    real_upload = api.upload_folder

    def capture(**kw):
        if kw.get("repo_type") == "dataset":
            published.update({p.name: p.read_text() for p in Path(kw["folder_path"]).glob("*.jsonl")})
        real_upload(**kw)

    api.upload_folder = capture
    run(pub, monkeypatch)
    assert api.tags["v1.0.0"] == api.head
    tagged = api.commits[api.tags["v1.0.0"]]
    assert tagged["model.safetensors"] == b"weights v1" and "README.md" in tagged
    labels = [json.loads(x) for x in published["human_labels.jsonl"].splitlines()]
    assert labels == [{"uid": "g-en-c0001-q0:gold", "kind": "grounding", "lang": "en", "human_score": 0.9,
                       "human_verdict": "grounded", "labeled_at": "2026-11-12T10:00:00"}]
    card = (tmp_path / "results" / "cards" / "model_README.md").read_text()
    top = card[: card.index("## Use it")]
    assert "license: cc-by-nc-4.0" in top and "Acceptable Use Policy" in top
    assert {"repo_id": "u/lorde-tiny-aya", "repo_type": "model", "private": True, "gated": False} in api.settings


def test_same_weights_new_card_updates_main_keeps_tag_and_goes_public(tmp_path, monkeypatch):
    """Private publish (e.g. Session D) creates v1.0.0; results change the card; --public under the same tag."""
    api = FakeApi()
    pub = world(tmp_path, monkeypatch, api)
    run(pub, monkeypatch)
    tag_at = api.tags["v1.0.0"]
    n_model = len(api.model_uploads())
    api.settings.clear()
    (tmp_path / "results").mkdir(exist_ok=True)
    (tmp_path / "results" / "judge_eval.md").write_text("# Judge eval\n\nnew numbers\n")
    run(pub, monkeypatch, "--public")
    assert api.tags["v1.0.0"] == tag_at  # the tag still pins the same weights
    new_uploads = api.model_uploads()[n_model:]
    assert len(new_uploads) == 1  # only the card: main already has these weights
    assert "new numbers" in api.commits[api.head]["README.md"].decode()
    assert "new numbers" not in api.commits[tag_at]["README.md"].decode()
    assert len(api.data_uploads()) == 2  # the data repo was published again
    assert {"repo_id": "u/lorde-tiny-aya", "repo_type": "model", "private": False, "gated": False} in api.settings
    assert {"repo_id": "u/lorde-data", "repo_type": "dataset", "private": False, "gated": False} in api.settings
    assert {"collection": "Lorde models", "private": False} in api.settings
    assert {"collection": "Lorde datasets", "private": False} in api.settings


def test_public_alone_flips_visibility_when_nothing_changed(tmp_path, monkeypatch):
    api = FakeApi()
    pub = world(tmp_path, monkeypatch, api)
    run(pub, monkeypatch)
    api.settings.clear()
    run(pub, monkeypatch, "--public")
    assert [s["private"] for s in api.settings] == [False, False, False, False]  # model, data, 2 collections


def test_main_drifted_from_the_tag_gets_the_tagged_weights_back(tmp_path, monkeypatch):
    api = FakeApi()
    pub = world(tmp_path, monkeypatch, api)
    run(pub, monkeypatch)
    api.commits["c9"] = {**api.commits[api.head], "model.safetensors": b"something else"}
    api.head = "c9"
    n_model = len(api.model_uploads())
    run(pub, monkeypatch)
    assert len(api.model_uploads()) == n_model + 2  # weights, then the card
    assert api.commits[api.head]["model.safetensors"] == b"weights v1"


def test_different_weights_under_an_existing_tag_fail_before_uploading(tmp_path, monkeypatch):
    api = FakeApi()
    pub = world(tmp_path, monkeypatch, api)
    run(pub, monkeypatch)
    tag_at, n = api.tags["v1.0.0"], len(api.uploads)
    (tmp_path / "runs" / "lorde-v1" / "merged" / "model.safetensors").write_bytes(b"weights v2")
    api.settings.clear()
    with pytest.raises(SystemExit) as e:
        run(pub, monkeypatch, "--public")
    msg = str(e.value)
    assert "--tag v1.0.1" in msg and "model.safetensors differs" in msg and "Nothing was uploaded" in msg
    assert len(api.uploads) == n and api.tags["v1.0.0"] == tag_at and not api.settings
    # a new version publishes the new weights and tags them, leaving v1.0.0 alone
    run(pub, monkeypatch, "--tag", "v1.0.1")
    assert api.tags["v1.0.0"] == tag_at and api.tags["v1.0.1"] == api.head
    assert api.commits[api.tags["v1.0.1"]]["model.safetensors"] == b"weights v2"


def test_a_missing_or_extra_weight_file_counts_as_different(tmp_path, monkeypatch):
    api = FakeApi()
    pub = world(tmp_path, monkeypatch, api)
    run(pub, monkeypatch)
    (tmp_path / "runs" / "lorde-v1" / "merged" / "tokenizer.json").unlink()
    with pytest.raises(SystemExit, match="tokenizer.json only on the Hub"):
        run(pub, monkeypatch)
    assert pub.bump("v1.0.9") == "v1.0.10"


def test_weight_file_patterns():
    pub = load_publish()
    for p in ("model-00001-of-00002.safetensors", "model.safetensors.index.json", "lorde-q4_k_m.gguf",
              "config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
              "special_tokens_map.json", "chat_template.jinja"):
        assert pub.is_weight_file(p), p
    for p in ("README.md", "template_fingerprints.json", "train_meta.json", ".gitattributes"):
        assert not pub.is_weight_file(p), p
