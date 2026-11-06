"""Единый предметный сервис Markdown и ZIP артефактов расследования."""

import datetime

from artifact_store import artifact_from_data_url
from evidence import with_investigation_chain
from reporting import build_investigation_package, build_report

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
    artifacts = [artifact_from_data_url(report_url, context)]
    last_report = {
        "title": title,
        "markdown": markdown,
        "filename": report_name,
        "bytes": report_bytes,
        "saved_at": saved_at or datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    if state.get("evidence"):
        package_name, package_url, package_bytes = build_investigation_package(
            title,
            markdown,
            state,
            saved_at=saved_at,
            filename=previous.get("package_filename"),
            verification=verification,
        )
        artifacts.append(artifact_from_data_url(package_url, context))
        last_report.update({"package_filename": package_name, "package_bytes": package_bytes})
    return last_report, artifacts
