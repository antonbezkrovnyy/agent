from types import SimpleNamespace

from artifact_store import artifact_from_data_url, artifact_url, put_artifact
from reporting import build_report
import artifact_store as artifacts


def test_inline_fallback_keeps_local_mode_compatible(monkeypatch):
    monkeypatch.setattr(artifacts._settings, "artifact_store_backend", "local")

    artifact = put_artifact("report.md", b"# report", "text/markdown", {"id": "case-1"})

    assert artifact["storage"] == "inline"
    assert artifact["key"].startswith("investigations/case-1/current/")
    assert artifact["key"].endswith("/report.md")
    assert artifact["sha256"][:16] in artifact["key"]
    assert artifact_url(artifact).startswith("data:text/markdown;base64,")


def test_artifact_keys_are_immutable_by_content_and_run(monkeypatch):
    monkeypatch.setattr(artifacts._settings, "artifact_store_backend", "local")

    first = put_artifact("report.md", b"one", "text/markdown", {"id": "case-1"}, "run-1")
    same = put_artifact("report.md", b"one", "text/markdown", {"id": "case-1"}, "run-1")
    changed = put_artifact("report.md", b"two", "text/markdown", {"id": "case-1"}, "run-1")
    next_run = put_artifact("report.md", b"one", "text/markdown", {"id": "case-1"}, "run-2")

    assert first["key"] == same["key"]
    assert changed["key"] != first["key"]
    assert next_run["key"] != first["key"]


def test_s3_upload_returns_key_and_fresh_signed_url(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            return None

    def request(method, url, **kwargs):
        requests.append((method, url, kwargs))
        return Response()

    monkeypatch.setattr(artifacts.httpx, "request", request)
    monkeypatch.setattr(artifacts.httpx, "put", lambda url, **kwargs: request("PUT", url, **kwargs))
    monkeypatch.setattr(artifacts._settings, "artifact_store_backend", "s3")
    monkeypatch.setattr(artifacts._settings, "artifact_s3_endpoint", "http://seaweedfs:8333")
    monkeypatch.setattr(artifacts._settings, "artifact_s3_public_endpoint", "http://localhost:8333")
    monkeypatch.setattr(artifacts._settings, "artifact_s3_access_key", "agent")
    monkeypatch.setattr(artifacts._settings, "artifact_s3_secret_key", "secret")
    artifacts._store = None
    real_datetime = artifacts.datetime

    class Clock:
        current = real_datetime.now(artifacts.UTC)

        @classmethod
        def now(cls, timezone=None):
            return cls.current

    monkeypatch.setattr(artifacts, "datetime", Clock)
    artifact = put_artifact("report.md", b"# report", "text/markdown", {"id": "case/1"}, "run 2")
    first = artifact_url(artifact)
    second = artifact_url(artifact)

    assert artifact["storage"] == "s3"
    assert artifact["key"] == f"investigations/case/1/run-2/{artifacts.hashlib.sha256(b'# report').hexdigest()[:16]}/report.md"
    assert "base64" not in str(artifact)
    assert first.startswith("http://localhost:8333/logicore-investigations/")
    assert "X-Amz-Signature=" in first
    assert "X-Amz-Expires=3600" in first
    assert second == first
    from datetime import timedelta

    Clock.current += timedelta(hours=2)
    refreshed = artifact_url(artifact)
    assert refreshed != first
    assert artifact["key"] == f"investigations/case/1/run-2/{artifacts.hashlib.sha256(b'# report').hexdigest()[:16]}/report.md"
    assert [item[0] for item in requests] == ["PUT", "PUT"]


def test_data_url_is_converted_to_metadata(monkeypatch):
    monkeypatch.setattr(artifacts._settings, "artifact_store_backend", "local")
    _, url, _ = build_report("Отчёт", "Вывод", "report.md")

    artifact = artifact_from_data_url(url, {"id": "case-2"})

    assert artifact["filename"].endswith("-report.md")
    assert artifact["bytes"] > 0
    assert artifact["storage"] == "inline"


def test_put_artifact_file_streams_and_hashes_content(monkeypatch, tmp_path):
    path = tmp_path / "bundle.zip"
    payload = b"zip-content" * 1000
    path.write_bytes(payload)
    requests = []

    class Response:
        def raise_for_status(self):
            return None

    def request(method, url, **kwargs):
        content = kwargs.get("content")
        if not isinstance(content, (bytes, type(None))):
            kwargs["content"] = b"".join(content)
        requests.append((method, url, kwargs))
        return Response()

    monkeypatch.setattr(artifacts.httpx, "request", request)
    monkeypatch.setattr(artifacts.httpx, "put", lambda url, **kwargs: request("PUT", url, **kwargs))
    monkeypatch.setattr(artifacts._settings, "artifact_store_backend", "s3")
    monkeypatch.setattr(artifacts._settings, "artifact_s3_endpoint", "http://seaweedfs:8333")
    artifacts._store = None

    artifact = artifacts.put_artifact_file(
        path.name, path, "application/zip", {"id": "case-stream"}, "run-stream",
    )

    uploaded = next(item for item in requests if item[1].endswith("bundle.zip"))
    content = uploaded[2]["content"]
    assert content == payload
    assert artifact["bytes"] == len(payload)
    assert artifact["sha256"] == artifacts.hashlib.sha256(payload).hexdigest()
    assert "data_url" not in artifact


def test_report_package_uses_streaming_upload(monkeypatch):
    import artifacts as artifact_service
    from tools import save_report
    from test_investigation_package import state_with_evidence

    uploaded = {}

    def upload(filename, path, content_type, context, run_id):
        uploaded["filename"] = filename
        uploaded["content"] = path.read_bytes()
        uploaded["run_id"] = run_id
        return {
            "storage": "s3",
            "key": "investigations/case-package/run/package.zip",
            "filename": filename,
            "content_type": content_type,
            "bytes": path.stat().st_size,
            "sha256": artifacts.hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    monkeypatch.setattr(artifact_service, "s3_enabled", lambda: True)
    monkeypatch.setattr(artifact_service, "put_artifact_file", upload)
    state = {**state_with_evidence(), "mcp_reuse_run_id": "run-report"}
    runtime = SimpleNamespace(state=state, tool_call_id="call-report-stream")

    result = save_report.func("Потоковый отчёт", "# Вывод", runtime=runtime)

    assert uploaded["run_id"] == "run-report"
    assert uploaded["filename"].endswith(".zip")
    assert uploaded["content"].startswith(b"PK")
    assert result.update["artifacts"][1]["storage"] == "s3"
    assert "data_url" not in result.update["artifacts"][1]
    assert result.update["last_report"]["package_sha256"] == result.update["artifacts"][1]["sha256"]


def test_existing_bucket_conflict_is_not_an_upload_failure(monkeypatch, tmp_path):
    path = tmp_path / "bundle.zip"
    path.write_bytes(b"zip")
    requests = []

    class Response:
        def __init__(self, url):
            self.status_code = 200 if url.endswith("bundle.zip") else 409

        def raise_for_status(self):
            if self.status_code >= 400:
                raise artifacts.httpx.HTTPStatusError("conflict", request=None, response=self)

    def request(method, url, **kwargs):
        requests.append((method, url, kwargs))
        return Response(url)

    monkeypatch.setattr(artifacts.httpx, "request", request)
    monkeypatch.setattr(artifacts.httpx, "put", lambda url, **kwargs: request("PUT", url, **kwargs))
    monkeypatch.setattr(artifacts._settings, "artifact_store_backend", "s3")
    monkeypatch.setattr(artifacts._settings, "artifact_s3_endpoint", "http://seaweedfs:8333")
    artifacts._store = None

    artifact = artifacts.put_artifact_file(
        path.name, path, "application/zip", {"id": "case-existing"}, "run-existing",
    )

    assert artifact["storage"] == "s3"
    assert artifact["bytes"] == 3
    assert len(requests) == 2
