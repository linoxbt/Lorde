"""Dataset and result storage: Supabase Storage in production, a local directory in dev and tests.

Both hand out short-lived signed URLs (15 minutes, spec Section 13.2) so clients upload and download
directly, never through the Gateway's request body.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

import httpx

SIGNED_URL_TTL_S = 15 * 60
CHUNK = 1024 * 1024


class TooLarge(ValueError):
    """An object is bigger than the caller allows."""


async def _chunks(src: Path) -> AsyncIterator[bytes]:
    with src.open("rb") as f:
        while chunk := f.read(CHUNK):
            yield chunk


class Storage(Protocol):
    async def signed_upload(self, bucket: str, path: str) -> dict: ...
    async def signed_download(self, bucket: str, path: str) -> str: ...
    async def download(self, bucket: str, path: str, dest: Path, max_bytes: int | None = None) -> int: ...
    async def upload(self, bucket: str, path: str, src: Path, content_type: str = "application/octet-stream") -> None: ...
    async def delete(self, bucket: str, paths: list[str]) -> None: ...


class SupabaseStorage:
    """Supabase Storage REST API with the service-role key (server side only)."""

    def __init__(self, url: str, service_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = url.rstrip("/") + "/storage/v1"
        self.client = httpx.AsyncClient(timeout=120, transport=transport,
                                        headers={"Authorization": f"Bearer {service_key}", "apikey": service_key})

    async def signed_upload(self, bucket: str, path: str) -> dict:
        r = await self.client.post(f"{self.base}/object/upload/sign/{bucket}/{path}")
        r.raise_for_status()
        return {"url": self.base + r.json()["url"], "method": "PUT", "headers": {},
                "expires_in": 7200}  # Supabase signed upload URLs last two hours

    async def signed_download(self, bucket: str, path: str) -> str:
        r = await self.client.post(f"{self.base}/object/sign/{bucket}/{path}", json={"expiresIn": SIGNED_URL_TTL_S})
        r.raise_for_status()
        return self.base + r.json()["signedURL"]

    async def download(self, bucket: str, path: str, dest: Path, max_bytes: int | None = None) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        async with self.client.stream("GET", f"{self.base}/object/{bucket}/{path}") as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in r.aiter_bytes():
                    n += len(chunk)
                    if max_bytes is not None and n > max_bytes:
                        raise TooLarge(f"object is larger than {max_bytes} bytes")
                    f.write(chunk)
        return n

    async def upload(self, bucket: str, path: str, src: Path, content_type: str = "application/octet-stream") -> None:
        r = await self.client.post(f"{self.base}/object/{bucket}/{path}", content=_chunks(src),
                                   headers={"x-upsert": "true", "content-type": content_type,
                                            "content-length": str(src.stat().st_size)})
        r.raise_for_status()

    async def delete(self, bucket: str, paths: list[str]) -> None:
        if paths:
            r = await self.client.request("DELETE", f"{self.base}/object/{bucket}", json={"prefixes": paths})
            r.raise_for_status()


class LocalStorage:
    """Files under a root directory; signed URLs point at the Gateway's /v1/_storage routes."""

    def __init__(self, root: str | Path, secret: str, public_base: str = "") -> None:
        self.root = Path(root)
        self.secret = secret.encode()
        self.public_base = public_base.rstrip("/")

    def _file(self, bucket: str, path: str) -> Path:
        p = (self.root / bucket / path).resolve()
        if not str(p).startswith(str((self.root / bucket).resolve()) + "/"):
            raise ValueError("path escapes the bucket")
        return p

    def token(self, op: str, bucket: str, path: str, ttl: int = SIGNED_URL_TTL_S) -> str:
        body = json.dumps({"op": op, "b": bucket, "p": path, "exp": int(time.time()) + ttl}).encode()
        sig = hmac.new(self.secret, body, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(body).decode().rstrip("=") + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")

    def check(self, token: str, op: str) -> tuple[str, str]:
        try:
            b64, s64 = token.split(".")
            body = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
            sig = base64.urlsafe_b64decode(s64 + "=" * (-len(s64) % 4))
        except Exception as e:  # noqa: BLE001
            raise PermissionError("bad token") from e
        if not hmac.compare_digest(sig, hmac.new(self.secret, body, hashlib.sha256).digest()):
            raise PermissionError("bad signature")
        d = json.loads(body)
        if d["op"] != op or d["exp"] < time.time():
            raise PermissionError("expired or wrong operation")
        return d["b"], d["p"]

    async def signed_upload(self, bucket: str, path: str) -> dict:
        return {"url": f"{self.public_base}/v1/_storage/{self.token('put', bucket, path)}", "method": "PUT",
                "headers": {}, "expires_in": SIGNED_URL_TTL_S}

    async def signed_download(self, bucket: str, path: str) -> str:
        return f"{self.public_base}/v1/_storage/{self.token('get', bucket, path)}"

    async def download(self, bucket: str, path: str, dest: Path, max_bytes: int | None = None) -> int:
        src = self._file(bucket, path)
        if max_bytes is not None and src.stat().st_size > max_bytes:
            raise TooLarge(f"object is larger than {max_bytes} bytes")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest.stat().st_size

    async def upload(self, bucket: str, path: str, src: Path, content_type: str = "application/octet-stream") -> None:
        dst = self._file(bucket, path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    async def delete(self, bucket: str, paths: list[str]) -> None:
        for p in paths:
            self._file(bucket, p).unlink(missing_ok=True)

    async def write_stream(self, bucket: str, path: str, chunks: AsyncIterator[bytes], max_bytes: int) -> int:
        """Stream an upload to disk (never whole in memory); the object appears only once complete."""
        dst = self._file(bucket, path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.{time.monotonic_ns()}.part")
        n = 0
        try:
            with tmp.open("wb") as f:
                async for chunk in chunks:
                    n += len(chunk)
                    if n > max_bytes:
                        raise TooLarge(f"upload over {max_bytes} bytes")
                    f.write(chunk)
            tmp.replace(dst)
        finally:
            tmp.unlink(missing_ok=True)
        return n

    def older_than(self, bucket: str, age_s: float) -> list[str]:
        """Paths of the bucket's files last written more than ``age_s`` ago (finished uploads only)."""
        root, cutoff, out = (self.root / bucket), time.time() - age_s, []
        if root.is_dir():
            for f in root.rglob("*"):
                if f.is_file() and not f.name.startswith(".") and f.stat().st_mtime < cutoff:
                    out.append(f.relative_to(root).as_posix())
        return out

    def write_bytes(self, bucket: str, path: str, data: bytes) -> None:
        dst = self._file(bucket, path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)

    def read_bytes(self, bucket: str, path: str) -> bytes:
        return self._file(bucket, path).read_bytes()


def from_settings(s) -> Storage | None:
    if s.supabase_url and s.supabase_service_key:
        return SupabaseStorage(s.supabase_url, s.supabase_service_key)
    if s.storage_dir:
        return LocalStorage(s.storage_dir, s.key_pepper + ":storage", s.public_url)
    return None
