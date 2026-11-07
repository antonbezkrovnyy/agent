"""Хранение артефактов и выдача короткоживущих ссылок без base64 в state."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx

from config import Settings


class ArtifactStore(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> str: ...


class S3ArtifactStore:
    def __init__(self, settings: Settings):
        self.endpoint = settings.artifact_s3_endpoint.rstrip("/")
        self.public_endpoint = (settings.artifact_s3_public_endpoint or self.endpoint).rstrip("/")
        self.region = settings.artifact_s3_region
        self.bucket = settings.artifact_s3_bucket
        self.access_key = settings.artifact_s3_access_key
        self.secret_key = settings.artifact_s3_secret_key
        self.ttl = settings.artifact_signed_url_ttl_seconds
        self._ready = False

    @staticmethod
    def _sign(key: str, data: bytes) -> str:
        return hmac.new(key, data, hashlib.sha256).hexdigest()

    def _headers(
        self, method: str, url: str, data: bytes, query: dict | None = None, *, payload_hash: str | None = None,
    ) -> dict:
        parts = urlsplit(url)
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        payload_hash = payload_hash or hashlib.sha256(data).hexdigest()
        headers = {
            "host": parts.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        signed_headers = ";".join(sorted(headers))
        canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
        canonical_query = urlencode(sorted((query or {}).items()), quote_via=quote)
        canonical = "\n".join([
            method,
            quote(parts.path, safe="/-_.~"),
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ])
        scope = f"{date}/{self.region}/s3/aws4_request"
        to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        ])
        key = self._sign(f"AWS4{self.secret_key}".encode(), date.encode()).encode()
        key = self._sign(key, self.region.encode()).encode()
        key = self._sign(key, b"s3").encode()
        signature = self._sign(self._sign(key, b"aws4_request").encode(), to_sign.encode())
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return headers

    def _request(self, method: str, key: str = "", data: bytes = b"") -> httpx.Response:
        url = f"{self.endpoint}/{self.bucket}" + (f"/{quote(key, safe='/')}" if key else "")
        response = httpx.request(method, url, headers=self._headers(method, url, data), content=data, timeout=30)
        response.raise_for_status()
        return response

    def _ensure_bucket(self) -> None:
        if self._ready:
            return
        try:
            self._request("PUT")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
        self._ready = True

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self._ensure_bucket()
        url = f"{self.endpoint}/{self.bucket}/{quote(key, safe='/')}"
        headers = self._headers("PUT", url, data)
        headers["Content-Type"] = content_type
        response = httpx.put(url, headers=headers, content=data, timeout=60)
        response.raise_for_status()
        return key

    def put_file(self, key: str, path: Path, content_type: str) -> str:
        self._ensure_bucket()
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        url = f"{self.endpoint}/{self.bucket}/{quote(key, safe='/')}"
        headers = self._headers("PUT", url, b"", payload_hash=digest.hexdigest())
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(path.stat().st_size)
        with path.open("rb") as source:
            response = httpx.put(
                url,
                headers=headers,
                content=iter(lambda: source.read(1024 * 1024), b""),
                timeout=120,
            )
        response.raise_for_status()
        return key

    def signed_url(self, key: str) -> str:
        url = f"{self.public_endpoint}/{self.bucket}/{quote(key, safe='/')}"
        parts = urlsplit(url)
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = now.strftime("%Y%m%d")
        scope = f"{date}/{self.region}/s3/aws4_request"
        payload_hash = "UNSIGNED-PAYLOAD"
        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(self.ttl),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_query = urlencode(sorted(query.items()), quote_via=quote)
        canonical = "\n".join([
            "GET", quote(parts.path, safe="/-_.~"), canonical_query,
            f"host:{parts.netloc}\n", "host", payload_hash,
        ])
        to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amz_date, scope,
            hashlib.sha256(canonical.encode()).hexdigest(),
        ])
        signing_key = self._sign(f"AWS4{self.secret_key}".encode(), date.encode()).encode()
        signing_key = self._sign(signing_key, self.region.encode()).encode()
        signing_key = self._sign(signing_key, b"s3").encode()
        signature = self._sign(self._sign(signing_key, b"aws4_request").encode(), to_sign.encode())
        query["X-Amz-Signature"] = signature
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(sorted(query.items()), quote_via=quote), ""))


_settings = Settings()
_store: S3ArtifactStore | None = None


def _s3_enabled() -> bool:
    return _settings.artifact_store_backend == "s3" or (
        _settings.artifact_store_backend == "auto" and bool(_settings.artifact_s3_endpoint)
    )


def _get_store() -> S3ArtifactStore:
    global _store
    if _store is None:
        _store = S3ArtifactStore(_settings)
    return _store


def artifact_url(artifact: dict | str) -> str:
    if isinstance(artifact, str):
        return artifact
    if artifact.get("storage") == "s3":
        return _get_store().signed_url(artifact["key"]) + "#" + quote(artifact["filename"])
    return artifact.get("data_url") or ""


def s3_enabled() -> bool:
    return _s3_enabled()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def put_artifact_file(
    filename: str, path: Path, content_type: str, context: dict, run_id: str = "current",
) -> dict:
    safe_run = "".join(char if char.isalnum() or char in "-_" else "-" for char in run_id)
    key = "/".join(filter(None, [
        _settings.artifact_s3_prefix.strip("/"),
        str(context.get("id") or "unassigned"),
        safe_run,
        filename,
    ]))
    if _s3_enabled():
        try:
            _get_store().put_file(key, path, content_type)
            return {
                "storage": "s3", "key": key, "filename": filename,
                "content_type": content_type, "bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        except Exception:
            if path.stat().st_size > _settings.artifact_inline_fallback_bytes:
                raise
    return put_artifact(filename, path.read_bytes(), content_type, context, run_id)


def put_artifact(filename: str, data: bytes, content_type: str, context: dict, run_id: str = "current") -> dict:
    safe_run = "".join(char if char.isalnum() or char in "-_" else "-" for char in run_id)
    key = "/".join(filter(None, [
        _settings.artifact_s3_prefix.strip("/"),
        str(context.get("id") or "unassigned"),
        safe_run,
        filename,
    ]))
    if _s3_enabled():
        try:
            _get_store().put_bytes(key, data, content_type)
            return {
                "storage": "s3", "key": key, "filename": filename,
                "content_type": content_type, "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        except Exception:
            if len(data) > _settings.artifact_inline_fallback_bytes:
                raise
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "storage": "inline", "key": key, "filename": filename,
        "content_type": content_type, "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_url": f"data:{content_type};base64,{encoded}#{quote(filename)}",
    }


def artifact_from_data_url(url: str, context: dict) -> dict:
    header, encoded = url.split("base64,", 1)
    content_type = header.removeprefix("data:").removesuffix(";")
    payload, _, fragment = encoded.partition("#")
    filename = fragment or "artifact"
    return put_artifact(filename, base64.b64decode(payload), content_type, context)
