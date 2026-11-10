"""Gating jobs: upload a dataset, run CuratorKIT with Lorde as the judge, download the results."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import httpx

if TYPE_CHECKING:
    from .client import Lorde

TERMINAL = {"succeeded", "failed", "cancelled"}


class Job:
    def __init__(self, client: Lorde, data: dict[str, Any]) -> None:
        self._c = client
        self.data = data

    id = property(lambda self: self.data["id"])
    status = property(lambda self: self.data["status"])
    counts = property(lambda self: self.data.get("counts") or {})
    error = property(lambda self: self.data.get("error"))

    def __repr__(self) -> str:
        return f"Job(id={self.id!r}, status={self.status!r})"

    def refresh(self) -> Job:
        self.data = self._c._request("GET", f"/v1/jobs/{self.id}")
        return self

    def wait(self, poll_s: float = 5.0, timeout_s: float | None = None) -> Job:
        """Block until the job succeeds, fails or is cancelled."""
        t0 = time.monotonic()
        while self.refresh().status not in TERMINAL:
            if timeout_s is not None and time.monotonic() - t0 > timeout_s:
                raise TimeoutError(f"job {self.id} still {self.status} after {timeout_s} s")
            time.sleep(poll_s)
        return self

    def cancel(self) -> Job:
        self.data = self._c._request("POST", f"/v1/jobs/{self.id}/cancel")
        return self

    def results(self) -> dict[str, Any]:
        return self._c._request("GET", f"/v1/jobs/{self.id}/results")

    def download(self, out_dir: str | Path) -> dict[str, Path]:
        """Save passed.jsonl, rejected.jsonl, report.md and the provenance files to ``out_dir``."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        saved = {}
        for name, url in self.results()["files"].items():
            ext = {"report": ".md"}.get(name, ".json" if name in ("manifest", "lexsi_provenance") else ".jsonl")
            dest = out / f"{name}{ext}"
            dest.write_bytes(self._c._storage("GET", url).content)
            saved[name] = dest
        return saved


class Jobs:
    def __init__(self, client: Lorde) -> None:
        self._c = client

    def upload(self, path: str | Path) -> str:
        """Upload a JSONL, CSV or Parquet file; returns the upload id."""
        p = Path(path)
        u = self._c._request("POST", "/v1/uploads", {"filename": p.name, "bytes": p.stat().st_size})
        with p.open("rb") as f:
            self._c._storage(u["method"], u["upload_url"], content=f.read(), headers=u.get("headers") or {})
        return u["upload_id"]

    def create(self, dataset: str | Path | None = None, *, dataset_url: str | None = None,
               gates: dict[str, float | None] | None = None, pii: bool = False, dedup: str = "exact") -> Job:
        """Start a gating job on a local file (uploaded first) or a Hub dataset (``hf://datasets/...``)."""
        if (dataset is None) == (dataset_url is None):
            raise ValueError("give exactly one of dataset (a local file) or dataset_url")
        body: dict[str, Any] = {"gates": gates or {"grounding": 0.7, "reward": 0.7},
                                "options": {"pii": pii, "dedup": dedup}}
        if dataset is not None:
            body["upload_id"] = self.upload(dataset)
        else:
            body["dataset_url"] = dataset_url
        return Job(self._c, self._c._request("POST", "/v1/jobs", body))

    def get(self, job_id: str) -> Job:
        return Job(self._c, self._c._request("GET", f"/v1/jobs/{job_id}"))


def storage_request(http: httpx.Client, base_url: str, method: str, url: str, **kw: Any) -> httpx.Response:
    """Signed storage URLs carry their own credential: never send the Lorde API key to them."""
    full = url if url.startswith(("http://", "https://")) else urljoin(base_url + "/", url.lstrip("/"))
    same_origin = full.startswith(base_url)
    r = (http.request(method, full, **kw) if same_origin
         else httpx.request(method, full, timeout=300, **kw))
    r.raise_for_status()
    return r
