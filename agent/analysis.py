import asyncio
import datetime
import json
from typing import Any
from uuid import uuid4

import httpx
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from artifact_store import put_artifact
from config import Settings
from contracts import EvidenceRecord, json_dumps

settings = Settings()

_DEFAULT_ANALYSIS_COLUMNS = (
    "uuid",
    "timestamp",
    "event_type",
    "action",
    "category_low",
    "subject",
    "object",
    "src_host",
    "src_ip",
    "dst_host",
    "dst_ip",
    "generator",
    "recv_ipv4",
    "mime",
    "original_time",
    "message",
)


def _message(call_id: str, name: str, payload: dict, *, error: bool = False) -> ToolMessage:
    return ToolMessage(
        content=json_dumps(payload, ensure_ascii=False, default=str),
        tool_call_id=call_id,
        name=name,
        **({"status": "error"} if error else {}),
    )


def _error_update(call_id: str, name: str, message: str, **details) -> Command:
    return Command(update={
        "messages": [_message(call_id, name, {"ok": False, "error": message, **details}, error=True)]
    })


def _result_text(result: Any) -> str:
    text = getattr(result, "text", None)
    if text:
        return text
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(item, dict) and item.get("type") == "text" for item in content):
        return "\n".join(item.get("text", "") for item in content)
    return json_dumps(content, ensure_ascii=False)


def _artifact(filename: str, data: bytes, content_type: str, context: dict, run_id: str):
    try:
        return put_artifact(filename, data, content_type, context, run_id), ""
    except Exception as exc:
        return None, f"Артефакт не сохранён в S3: {type(exc).__name__}: {exc}"


@tool
async def analysis_dataset_create(
    iql: str | None = None,
    columns: list[str] | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    max_rows: int | None = None,
    runtime: ToolRuntime = None,
) -> Command:
    """Создать полный JSONL-датасет через extract_data для последующего analysis_sql."""
    state = runtime.state if runtime else {}
    call_id = runtime.tool_call_id if runtime else f"dataset-{uuid4().hex}"
    context = state.get("context") or {}
    start = time_from or context.get("time_from")
    end = time_to or context.get("time_to")
    if not start or not end:
        return _error_update(call_id, "analysis_dataset_create", "Для датасета нужны time_from и time_to или активное окно")
    limit = max_rows or settings.analysis_dataset_max_rows
    if not 1 <= limit <= settings.analysis_dataset_max_rows:
        return _error_update(
            call_id,
            "analysis_dataset_create",
            f"max_rows должен быть 1..{settings.analysis_dataset_max_rows}",
        )
    if columns is not None and (not columns or len(columns) > 20):
        return _error_update(call_id, "analysis_dataset_create", "columns: от 1 до 20 имён полей")

    from tools import _MCP_TOOLS_BY_NAME, build_mcp_evidence, extract_data_tool

    if extract_data_tool is None:
        return _error_update(call_id, "analysis_dataset_create", "Инструмент extract_data недоступен")
    investigation_id = context.get("id")
    run_id = state.get("mcp_reuse_run_id") or None
    requested_columns = list(columns or _DEFAULT_ANALYSIS_COLUMNS)
    lookup_tool = _MCP_TOOLS_BY_NAME.get("field_lookup")
    if lookup_tool is None:
        return _error_update(call_id, "analysis_dataset_create", "Справочник field_lookup недоступен")
    try:
        raw_lookup = await lookup_tool.ainvoke({"fields": requested_columns})
        lookup_data = json.loads(_result_text(raw_lookup))
    except Exception as exc:
        lookup_data = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    lookup_evidence = build_mcp_evidence(
        "field_lookup",
        {"fields": requested_columns},
        lookup_data,
        f"{call_id}-schema",
        investigation_id,
        run_id,
        call_id,
    )
    if lookup_data.get("ok") is False:
        return Command(update={
            "messages": [_message(
                call_id,
                "analysis_dataset_create",
                {"ok": False, "error": lookup_data.get("error") or "Схема полей недоступна"},
                error=True,
            )],
            "evidence": [lookup_evidence],
        })
    lookup_fields = {item.get("field") for item in lookup_data.get("fields") or [] if item.get("ok")}
    effective_columns = [name for name in requested_columns if name in lookup_fields]
    unavailable_columns = [name for name in requested_columns if name not in lookup_fields]
    if not effective_columns:
        return Command(update={
            "messages": [_message(
                call_id,
                "analysis_dataset_create",
                {"ok": False, "error": "Ни одно запрошенное поле не существует в схеме", "unavailable_columns": unavailable_columns},
                error=True,
            )],
            "evidence": [lookup_evidence],
        })
    query = {
        "time_from": start,
        "time_to": end,
        "iql": iql,
        "columns": effective_columns,
        "order": "asc",
        "max_rows": limit,
    }
    reusable = next((
        item for item in reversed(state.get("analysis_datasets") or [])
        if item.get("run_id") == run_id and item.get("complete") and item.get("query") == query
    ), None)
    if reusable is not None:
        result = {
            "ok": True,
            "dataset_id": reusable["id"],
            "row_count": reusable.get("row_count") or 0,
            "columns": reusable.get("columns") or [],
            "complete": True,
            "pages": reusable.get("pages") or [],
            "source_evidence_ids": reusable.get("source_evidence_ids") or [],
            "rows_preview": (reusable.get("rows") or [])[:3],
            "reused": True,
            "reused_from": reusable["id"],
        }
        return Command(update={"messages": [_message(call_id, "analysis_dataset_create", result)]})
    rows: list[dict] = []
    evidence: list[dict] = [lookup_evidence]
    pages: list[dict] = []
    seen_pages: set[int] = set()
    page = 1
    complete = False
    error = ""
    while len(rows) < limit:
        seen_pages.add(page)
        args = {
            "time_from": start,
            "time_to": end,
            "iql": iql,
            "columns": effective_columns,
            "max_rows": min(5000, limit - len(rows)),
            "page": page,
            "order": "asc",
        }
        evidence_id = f"{call_id}-extract-{page}"
        try:
            raw = await asyncio.wait_for(
                extract_data_tool.ainvoke(args),
                timeout=settings.export_timeout_seconds,
            )
            data = json.loads(_result_text(raw))
            if not isinstance(data, dict):
                data = {"result": data}
        except Exception as exc:
            data = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        evidence.append(build_mcp_evidence(
            "extract_data", args, data, evidence_id, investigation_id, run_id, call_id,
        ))
        pages.append({
            "page": page,
            "evidence_id": evidence_id,
            "returned_rows": data.get("returned_rows", 0),
            "complete": data.get("complete"),
            "next_page": data.get("next_page"),
            "error": data.get("error"),
        })
        if data.get("ok") is False:
            error = str(data.get("error") or "extract_data failed")
            break
        rows.extend(item for item in data.get("events") or [] if isinstance(item, dict))
        next_page = data.get("next_page")
        if data.get("complete") is True and next_page is None:
            complete = True
            break
        if next_page is None:
            error = str(data.get("hint") or "Выгрузка неполная")
            break
        if int(next_page) in seen_pages:
            error = f"extract_data повторил страницу {next_page}; выгрузка остановлена"
            break
        seen_pages.add(page)
        page = int(next_page)
    if len(rows) >= limit and not complete:
        error = error or f"Достигнут max_rows={limit}; датасет неполный"
    if unavailable_columns:
        complete = False
        error = error or f"Поля отсутствуют в схеме: {', '.join(unavailable_columns)}"

    dataset_id = f"dataset-{call_id}"
    dataset = {
        "id": dataset_id,
        "investigation_id": investigation_id,
        "run_id": run_id,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
        "query": query,
        "requested_columns": requested_columns,
        "unavailable_columns": unavailable_columns,
        "columns": list(dict.fromkeys([
            *effective_columns,
            *(key for row in rows for key in row),
        ])),
        "rows": rows,
        "row_count": len(rows),
        "complete": complete,
        "pages": pages,
        "source_evidence_ids": [item["id"] for item in evidence],
        "error": error,
    }
    payload = (
        "\n".join(json_dumps(row, ensure_ascii=False, default=str) for row in rows) + "\n"
    ).encode("utf-8")
    artifact, artifact_error = _artifact(
        f"{dataset_id}.jsonl", payload, "application/x-ndjson", context, run_id or "current",
    )
    result = {
        "ok": not error or bool(rows),
        "dataset_id": dataset_id,
        "row_count": len(rows),
        "columns": dataset["columns"],
        "complete": complete,
        "pages": pages,
        "source_evidence_ids": dataset["source_evidence_ids"],
        "unavailable_columns": unavailable_columns,
        "rows_preview": rows[:3],
        "artifact": artifact,
        "error": error or None,
        "artifact_error": artifact_error or None,
    }
    return Command(update={
        "messages": [_message(call_id, "analysis_dataset_create", result, error=not result["ok"])],
        "evidence": evidence,
        "analysis_datasets": [dataset],
        "artifacts": [artifact] if artifact else [],
    })


@tool
async def analysis_sql(
    dataset_id: str,
    query: str,
    max_rows: int | None = None,
    runtime: ToolRuntime = None,
) -> Command:
    """Выполнить один read-only SELECT над датасетом и сохранить полный результат."""
    state = runtime.state if runtime else {}
    call_id = runtime.tool_call_id if runtime else f"analysis-{uuid4().hex}"
    dataset = next((item for item in state.get("analysis_datasets") or [] if item.get("id") == dataset_id), None)
    if dataset is None:
        available = [item.get("id") for item in state.get("analysis_datasets") or []]
        return _error_update(call_id, "analysis_sql", f"Датасет не найден. Доступно: {available}")
    limit = max_rows or settings.analysis_result_max_rows
    if not 1 <= limit <= settings.analysis_result_max_rows:
        return _error_update(call_id, "analysis_sql", f"max_rows должен быть 1..{settings.analysis_result_max_rows}")
    run_id = state.get("mcp_reuse_run_id") or None
    attempt = 1 + sum(
        1 for item in state.get("analysis_results") or [] if item.get("run_id") == run_id
    )
    if attempt > settings.analysis_sql_max_attempts:
        return _error_update(
            call_id,
            "analysis_sql",
            f"Достигнут лимит analysis_sql={settings.analysis_sql_max_attempts} на run; "
            "исправьте стратегию или используйте aggregate_events",
            error_code="sql_attempt_limit",
            attempt=attempt,
            max_attempts=settings.analysis_sql_max_attempts,
        )
    reusable = next((
        item for item in reversed(state.get("analysis_results") or [])
        if item.get("dataset_id") == dataset_id
        and item.get("query") == query
        and item.get("max_rows") == limit
        and item.get("complete")
        and item.get("run_id") == (state.get("mcp_reuse_run_id") or None)
    ), None)
    if reusable is not None:
        public = {
            **reusable,
            "rows": (reusable.get("rows") or [])[:20],
            "rows_preview": (reusable.get("rows") or [])[:20],
            "evidence_id": reusable["id"],
            "reused": True,
            "reused_from": reusable["id"],
        }
        return Command(update={"messages": [_message(call_id, "analysis_sql", public)]})
    payload = {
        "dataset_id": dataset_id,
        "rows": dataset.get("rows") or [],
        "columns": dataset.get("columns") or [],
        "query": query,
        "max_rows": limit,
    }
    headers = {"Authorization": f"Bearer {settings.analysis_service_token}"} if settings.analysis_service_token else {}
    try:
        async with httpx.AsyncClient(timeout=settings.analysis_timeout_seconds) as client:
            response = await client.post(
                f"{settings.analysis_service_url.rstrip('/')}/analyze",
                json=payload,
                headers=headers,
            )
        data = response.json()
        if response.status_code != 200:
            data = {"ok": False, "error": f"Analysis service HTTP {response.status_code}", "details": data}
    except Exception as exc:
        data = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    complete = bool(data.get("ok")) and not data.get("truncated") and bool(dataset.get("complete"))
    status = "error" if data.get("ok") is False else ("ok" if complete else "partial")
    context = state.get("context") or {}
    query_context = dataset.get("query") or {}
    result = {
        **data,
        "dataset_id": dataset_id,
        "dataset_query": query_context,
        "dataset_complete": dataset.get("complete"),
        "source_evidence_ids": dataset.get("source_evidence_ids") or [],
        "query": query,
        "max_rows": limit,
        "run_id": run_id,
        "attempt": attempt,
        "max_attempts": settings.analysis_sql_max_attempts,
        "complete": complete,
    }
    evidence = EvidenceRecord(
        id=call_id,
        investigation_id=context.get("id"),
        run_id=state.get("mcp_reuse_run_id") or None,
        step_id=call_id,
        tool="analysis_sql",
        arguments={"dataset_id": dataset_id, "query": query, "max_rows": limit},
        window={
            "from": query_context.get("time_from"),
            "to": query_context.get("time_to"),
            "source": "analysis-dataset",
        },
        status=status,
        complete=status == "ok",
        result_ref=f"evidence://{context.get('id')}/{call_id}",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
        result=result,
        error=data.get("error"),
    ).model_dump()
    artifact, artifact_error = _artifact(
        f"{call_id}.json",
        json_dumps(result, ensure_ascii=False, default=str).encode("utf-8"),
        "application/json",
        context,
        state.get("mcp_reuse_run_id") or "current",
    )
    public = {
        **result,
        "rows": (result.get("rows") or [])[:20],
        "rows_preview": (result.get("rows") or [])[:20],
        "evidence_id": call_id,
        "artifact": artifact,
        "artifact_error": artifact_error or None,
    }
    return Command(update={
        "messages": [_message(call_id, "analysis_sql", public, error=status == "error")],
        "evidence": [evidence],
        "analysis_results": [{"id": call_id, **result}],
        "artifacts": [artifact] if artifact else [],
    })
