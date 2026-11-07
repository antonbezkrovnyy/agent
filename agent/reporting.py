"""Механика отчётов: сборка файла (data-URL) и доставка в callback.

Чистые функции без LangGraph — их можно тестировать в отрыве от графа.
"""

import base64
import csv
import datetime
import hashlib
import io
import re
import tempfile
import zipfile
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Settings
from contracts import json_dumps
from evidence import queries_markdown, referenced_evidence_ids
from provenance import source_metadata, time_metadata

settings = Settings()
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def slug(value: str, fallback: str) -> str:
    """Безопасное имя файла: только A-Za-z0-9._-."""
    segment = _SAFE_SEGMENT.sub("-", value.strip()).strip("-.")
    if not any(c.isascii() and c.isalpha() for c in segment):
        return fallback
    return segment or fallback


def build_report(
    title: str,
    markdown: str,
    filename: str | None = None,
    *,
    saved_at: str | None = None,
    exact_filename: bool = False,
) -> tuple[str, str, int]:
    """Собирает (имя файла, data-URL отчёта, размер в байтах)."""
    name = slug(filename, "report") if filename else slug(title, "report")
    if not name.lower().endswith(".md"):
        name += ".md"
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H-%M-%S")
    fname = name if exact_filename and filename else f"{stamp}-{name}"
    now = saved_at or datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    body = (
        "---\n"
        f"title: {json_dumps(title, ensure_ascii=False)}\n"
        f"saved_at: {now}\n"
        "---\n\n" + markdown.rstrip() + "\n"
    )
    data = body.encode("utf-8")
    url = f"data:text/markdown;base64,{base64.b64encode(data).decode('ascii')}#{fname}"
    return fname, url, len(data)


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json_dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _events_csv(state: dict) -> tuple[str, int]:
    rows = []
    for record in state.get("evidence") or []:
        result = record.get("result") or {}
        for event in result.get("events") or []:
            if isinstance(event, dict):
                rows.append({
                    **event,
                    "_evidence_id": record.get("id"),
                    "_tool": record.get("tool"),
                })
    columns = ["_evidence_id", "_tool"]
    columns.extend(dict.fromkeys(key for row in rows for key in row if key not in columns))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in columns})
    return output.getvalue(), len(rows)


def memory_audit_markdown(operations: list[dict], error: str = "") -> str:
    lines = ["## Память расследования"]
    if not operations:
        lines.append("LLM не предложила фактов для долговременной памяти." if not error else "Аудит консолидации недоступен.")
    for item in operations:
        action = item.get("action")
        requested = item.get("requested_action")
        actual = f"{requested} → {action}" if requested != action else str(action)
        status = {"saved": "сохранено", "rejected": "отклонено", "ignored": "проигнорировано"}.get(item.get("status"), item.get("status"))
        target = item.get("fact_id") or item.get("target_id") or "—"
        lines.append(f"- **{status}**, `{actual}`, факт `{target}`: {item.get('statement') or 'без нового факта'}")
        if item.get("reason"):
            lines.append(f"  Причина: {item['reason']}")
        if item.get("error"):
            lines.append(f"  Ошибка: `{item['error']}`")
    if error:
        lines.append(f"\nОграничение: {error}")
    return "\n".join(lines)


def package_validation(state: dict) -> dict:
    evidence = list(state.get("evidence") or [])
    calls = list(state.get("tool_calls") or [])
    evidence_ids = {item.get("id") for item in evidence}
    call_ids = [item.get("id") for item in calls]
    missing_result = [item.get("id") for item in evidence if "result" not in item]
    unknown_claim_ids = sorted(set(referenced_evidence_ids(state)) - evidence_ids)
    duplicate_call_ids = sorted({item for item in call_ids if call_ids.count(item) > 1})
    missing_call_evidence = sorted({
        item.get("evidence_id") for item in calls
        if item.get("evidence_id") and item.get("evidence_id") not in evidence_ids
    })
    return {
        "ok": not any((missing_result, unknown_claim_ids, duplicate_call_ids, missing_call_evidence)),
        "missing_result_evidence_ids": missing_result,
        "unknown_claim_evidence_ids": unknown_claim_ids,
        "duplicate_tool_call_ids": duplicate_call_ids,
        "missing_tool_call_evidence_ids": missing_call_evidence,
        "self_contained": not missing_result,
    }


def _package_files(
    title: str,
    markdown: str,
    state: dict,
    *,
    saved_at: str | None,
    filename: str | None,
    verification: dict | None,
) -> tuple[dict[str, bytes], str, str]:
    evidence = list(state.get("evidence") or [])
    tool_calls = list(state.get("tool_calls") or [])
    now = saved_at or datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    report_body = (
        "---\n"
        f"title: {json_dumps(title, ensure_ascii=False)}\n"
        f"saved_at: {now}\n"
        "---\n\n" + markdown.rstrip() + "\n"
    )
    events_csv, events_count = _events_csv(state)
    files = {
        "report.md": report_body.encode("utf-8"),
        "queries.md": queries_markdown(state).encode("utf-8"),
        "evidence.jsonl": (
            "\n".join(json_dumps(item, ensure_ascii=False, default=str) for item in evidence) + "\n"
        ).encode("utf-8"),
        "tool_calls.jsonl": (
            "\n".join(json_dumps(item, ensure_ascii=False, default=str) for item in tool_calls) + "\n"
        ).encode("utf-8"),
        "events.csv": events_csv.encode("utf-8"),
    }
    dataset_entries = []
    for index, dataset in enumerate(state.get("analysis_datasets") or [], start=1):
        path = f"datasets/{index}-{slug(str(dataset.get('id') or ''), 'dataset')}.jsonl"
        files[path] = (
            "\n".join(json_dumps(row, ensure_ascii=False, default=str) for row in dataset.get("rows") or []) + "\n"
        ).encode("utf-8")
        dataset_entries.append({
            "id": dataset.get("id"),
            "path": path,
            "query": dataset.get("query") or {},
            "columns": dataset.get("columns") or [],
            "requested_columns": dataset.get("requested_columns") or dataset.get("columns") or [],
            "unavailable_columns": dataset.get("unavailable_columns") or [],
            "row_count": dataset.get("row_count") or 0,
            "complete": dataset.get("complete"),
            "pages": dataset.get("pages") or [],
            "source_evidence_ids": dataset.get("source_evidence_ids") or [],
            "error": dataset.get("error") or "",
        })
    analysis_entries = []
    for index, result in enumerate(state.get("analysis_results") or [], start=1):
        path = f"analysis_results/{index}-{slug(str(result.get('id') or ''), 'analysis')}.json"
        files[path] = (json_dumps(result, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        analysis_entries.append({
            "id": result.get("id"),
            "path": path,
            "dataset_id": result.get("dataset_id"),
            "query": result.get("query"),
            "returned_rows": result.get("returned_rows") or 0,
            "complete": result.get("complete"),
        })
    context = state.get("context") or {}
    source = source_metadata(state)
    time_semantics, time_axes = time_metadata(state)
    manifest = {
        "investigation_id": context.get("id"),
        "created_at": now,
        "evidence_count": len(evidence),
        "tool_call_count": len(tool_calls),
        "reused_tool_call_count": sum(1 for item in tool_calls if item.get("reused")),
        "events_count": events_count,
        "evidence_index": {item.get("id"): index for index, item in enumerate(evidence, start=1)},
        "tool_call_index": {item.get("id"): index for index, item in enumerate(tool_calls, start=1)},
        "package_validation": package_validation(state),
        "incomplete_evidence_ids": [item.get("id") for item in evidence if item.get("status") != "ok"],
        "memory_operations": list(state.get("memory_operations") or []),
        "memory_error": state.get("memory_error") or "",
        "investigation_scope": state.get("investigation_scope") or {},
        "run_metrics": list(state.get("run_metrics") or []),
        "analysis_datasets": dataset_entries,
        "analysis_results": analysis_entries,
        "verification": verification or {"status": "not_reviewed"},
        "provenance": {
            **source,
            "signed": False,
            "agent_prompt_version": settings.agent_prompt_version,
            "time_semantics": time_semantics,
            "time_axes": time_axes,
            "independent_verification": state.get("independent_verification") or {"status": "not_performed"},
        },
        "files": [
            {
                "path": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in files.items()
        ],
    }
    manifest_bytes = (json_dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    files["manifest.json"] = manifest_bytes
    if not filename:
        name = slug(title, "investigation")
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H-%M-%S")
        filename = f"{stamp}-{name}.zip"
    return files, filename, hashlib.sha256(manifest_bytes).hexdigest()


def write_investigation_package(
    title: str,
    markdown: str,
    state: dict,
    directory: Path,
    *,
    saved_at: str | None = None,
    filename: str | None = None,
    verification: dict | None = None,
) -> tuple[str, Path, int, str]:
    """Записать самодостаточный ZIP во временный файл без data URL."""
    files, filename, manifest_sha256 = _package_files(
        title, markdown, state, saved_at=saved_at, filename=filename, verification=verification,
    )
    directory.mkdir(parents=True, exist_ok=True)
    archive_path = directory / filename
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    return filename, archive_path, archive_path.stat().st_size, manifest_sha256


def build_investigation_package(
    title: str,
    markdown: str,
    state: dict,
    *,
    saved_at: str | None = None,
    filename: str | None = None,
    verification: dict | None = None,
) -> tuple[str, str, int]:
    """Сформировать data URL для совместимости с локальными артефактами."""
    with tempfile.TemporaryDirectory(prefix="logicore-package-") as directory:
        filename, archive_path, size, _ = write_investigation_package(
            title,
            markdown,
            state,
            Path(directory),
            saved_at=saved_at,
            filename=filename,
            verification=verification,
        )
        data = archive_path.read_bytes()
    url = f"data:application/zip;base64,{base64.b64encode(data).decode('ascii')}#{filename}"
    return filename, url, size


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def post_report(target: str, payload: dict) -> httpx.Response:
    """POST с ретраями: 3 попытки, экспоненциальная задержка 1с → 2с → 4с."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await client.post(target, json=payload)
