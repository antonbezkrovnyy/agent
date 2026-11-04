"""Единый предметный сервис Markdown и ZIP артефактов расследования."""

from artifact_store import artifact_from_data_url
from evidence import with_investigation_chain
from reporting import build_investigation_package, build_report


def create_report_artifacts(
    title: str,
    markdown: str,
    state: dict,
    filename: str | None = None,
) -> tuple[dict, list[dict]]:
    markdown = with_investigation_chain(markdown, state)
    report_name, report_url, report_bytes = build_report(title, markdown, filename)
    context = state.get("context") or {}
    artifacts = [artifact_from_data_url(report_url, context)]
    last_report = {
        "title": title,
        "markdown": markdown,
        "filename": report_name,
        "bytes": report_bytes,
    }
    if state.get("evidence"):
        package_name, package_url, package_bytes = build_investigation_package(title, markdown, state)
        artifacts.append(artifact_from_data_url(package_url, context))
        last_report.update({"package_filename": package_name, "package_bytes": package_bytes})
    return last_report, artifacts
