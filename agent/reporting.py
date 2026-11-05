"""Механика отчётов: сборка файла (data-URL) и доставка в callback.

Чистые функции без LangGraph — их можно тестировать в отрыве от графа.
"""

import base64
import csv
import datetime
import hashlib
import io
import re
import zipfile

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from contracts import json_dumps
from evidence import queries_markdown

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def slug(value: str, fallback: str) -> str:
    """Безопасное имя файла: только A-Za-z0-9._-."""
    segment = _SAFE_SEGMENT.sub("-", value.strip()).strip("-.")
    if not any(c.isascii() and c.isalpha() for c in segment):
        return fallback
    return segment or fallback


def build_report(title: str, markdown: str, filename: str | None = None) -> tuple[str, str, int]:
    """Собирает (имя файла, data-URL отчёта, размер в байтах)."""
    name = slug(filename, "report") if filename else slug(title, "report")
    if not name.lower().endswith(".md"):
        name += ".md"
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H-%M-%S")
    fname = f"{stamp}-{name}"
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
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


def build_investigation_package(title: str, markdown: str, state: dict) -> tuple[str, str, int]:
    """Сформировать ZIP с отчётом, запросами, Evidence и событиями."""
    evidence = list(state.get("evidence") or [])
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
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
        "events.csv": events_csv.encode("utf-8"),
    }
    context = state.get("context") or {}
    manifest = {
        "investigation_id": context.get("id"),
        "created_at": now,
        "evidence_count": len(evidence),
        "events_count": events_count,
        "evidence_index": {item.get("id"): index for index, item in enumerate(evidence, start=1)},
        "incomplete_evidence_ids": [item.get("id") for item in evidence if item.get("status") != "ok"],
        "memory_operations": list(state.get("memory_operations") or []),
        "memory_error": state.get("memory_error") or "",
        "investigation_scope": state.get("investigation_scope") or {},
        "run_metrics": list(state.get("run_metrics") or []),
        "files": [
            {
                "path": name,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in files.items()
        ],
    }
    files["manifest.json"] = (json_dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    data = archive.getvalue()
    name = slug(title, "investigation")
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H-%M-%S")
    filename = f"{stamp}-{name}.zip"
    url = f"data:application/zip;base64,{base64.b64encode(data).decode('ascii')}#{filename}"
    return filename, url, len(data)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def post_report(target: str, payload: dict) -> httpx.Response:
    """POST с ретраями: 3 попытки, экспоненциальная задержка 1с → 2с → 4с."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await client.post(target, json=payload)
