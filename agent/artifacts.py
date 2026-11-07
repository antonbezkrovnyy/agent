"""Единый предметный сервис Markdown и ZIP артефактов расследования."""

import datetime
import tempfile
from pathlib import Path

from artifact_store import artifact_from_data_url, put_artifact_file, s3_enabled
from evidence import with_investigation_chain
from reporting import build_investigation_package, build_report, write_investigation_package

VERIFICATION_HEADING = "## Статус проверки и доверие"


def strip_verification(markdown: str) -> str:
    marker = "\n\n---\n\n" + VERIFICATION_HEADING
    return markdown.split(marker, 1)[0].rstrip() + "\n"


def create_report_artifacts(
    title: str,
    markdown: str,
    state: dict,
    filename: str | None = None,
    *,
    previous: dict | None = None,
    verification_block: str | None = None,
    verification: dict | None = None,
) -> tuple[dict, list[dict]]:
    markdown = with_investigation_chain(strip_verification(markdown), state)
    if verification_block:
        markdown = markdown.rstrip() + "\n\n---\n\n" + verification_block + "\n"
    previous = previous or {}
    saved_at = previous.get("saved_at")
    report_name, report_url, report_bytes = build_report(
        title,
        markdown,
        previous.get("filename") or filename,
        saved_at=saved_at,
        exact_filename=bool(previous.get("filename")),
    )
    context = state.get("context") or {}
    run_id = state.get("mcp_reuse_run_id") or "current"
    artifacts = [artifact_from_data_url(report_url, context, run_id)]
    last_report = {
        "title": title,
        "markdown": markdown,
        "filename": report_name,
        "bytes": report_bytes,
        "saved_at": saved_at or datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    if state.get("evidence"):
        if s3_enabled():
            with tempfile.TemporaryDirectory(prefix="logicore-package-") as directory:
                package_name, package_path, package_bytes, manifest_digest = write_investigation_package(
                    title,
                    markdown,
                    state,
                    Path(directory),
                    saved_at=saved_at,
                    filename=previous.get("package_filename"),
                    verification=verification,
                )
                package_artifact = put_artifact_file(
                    package_path.name, package_path, "application/zip", context, run_id,
                )
        else:
            package_name, package_url, package_bytes = build_investigation_package(
                title,
                markdown,
                state,
                saved_at=saved_at,
                filename=previous.get("package_filename"),
                verification=verification,
            )
            package_artifact = artifact_from_data_url(package_url, context, run_id)
            encoded = package_url.split("base64,", 1)[1].split("#", 1)[0]
            import base64
            import hashlib
            import io
            import zipfile

            with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as bundle:
                manifest_digest = hashlib.sha256(bundle.read("manifest.json")).hexdigest()
        package_artifact["manifest_sha256"] = manifest_digest
        artifacts.append(package_artifact)
        last_report.update({
            "package_filename": package_name,
            "package_bytes": package_bytes,
            "package_sha256": package_artifact["sha256"],
            "manifest_sha256": manifest_digest,
        })
    from knowledge import publish_report_card

    report_result = state.get("report_result") or {}
    verification_data = verification or report_result.get("verification") or {}
    publish_report_card({
        "case_id": str(context.get("id") or "unassigned"),
        "run_id": run_id,
        "title": title,
        "summary": str(report_result.get("answer") or markdown)[:4000],
        "verification_status": verification_data.get("status") or "not_reviewed",
        "saved_at": last_report["saved_at"],
        "artifacts": [{
            "key": item.get("key"),
            "filename": item.get("filename"),
            "sha256": item.get("sha256"),
        } for item in artifacts],
    })
    return last_report, artifacts
