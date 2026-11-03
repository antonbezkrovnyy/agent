from artifact_store import artifact_from_data_url, artifact_url, put_artifact
from reporting import build_report
import artifact_store as artifacts


def test_inline_fallback_keeps_local_mode_compatible(monkeypatch):
    monkeypatch.setattr(artifacts._settings, "artifact_store_backend", "local")

    artifact = put_artifact("report.md", b"# report", "text/markdown", {"id": "case-1"})

    assert artifact["storage"] == "inline"
    assert artifact["key"].startswith("investigations/case-1/current/report.md")
    assert artifact_url(artifact).startswith("data:text/markdown;base64,")


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

    artifact = put_artifact("report.md", b"# report", "text/markdown", {"id": "case/1"}, "run 2")
    first = artifact_url(artifact)
    second = artifact_url(artifact)

    assert artifact["storage"] == "s3"
    assert artifact["key"] == "investigations/case/1/run-2/report.md"
    assert "base64" not in str(artifact)
    assert first.startswith("http://localhost:8333/logicore-investigations/")
    assert "X-Amz-Signature=" in first
    assert "X-Amz-Expires=3600" in first
    assert second == first
    assert [item[0] for item in requests] == ["PUT", "PUT"]


def test_data_url_is_converted_to_metadata(monkeypatch):
    monkeypatch.setattr(artifacts._settings, "artifact_store_backend", "local")
    _, url, _ = build_report("Отчёт", "Вывод", "report.md")

    artifact = artifact_from_data_url(url, {"id": "case-2"})

    assert artifact["filename"].endswith("-report.md")
    assert artifact["bytes"] > 0
    assert artifact["storage"] == "inline"
