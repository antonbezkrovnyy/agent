"""Агрегированные данные рабочего стола аналитика без демонстрационных заглушек."""

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg

from aegra_client import AegraClient

DATABASE_URL = os.environ.get("AGENT_MEMORY_DATABASE_URL", "")


def _content(message: dict) -> str:
    value = message.get("content") or ""
    if isinstance(value, str):
        return value
    return ""


def _messages(values: dict) -> list[dict]:
    return values.get("messages") or []


def _title(thread: dict, values: dict) -> str:
    metadata_name = (thread.get("metadata") or {}).get("thread_name")
    if metadata_name:
        return str(metadata_name)
    first_user = next((message for message in _messages(values) if (message.get("type") or message.get("role")) in ("human", "user")), None)
    return _content(first_user or {})[:140] or "Диалог без названия"


def _last_message(values: dict) -> str:
    messages = _messages(values)
    return _content(messages[-1])[:220] if messages else "Сообщений пока нет"


def _thread_view(thread: dict, state: dict, runs: list[dict]) -> dict:
    values = state.get("values") or {}
    latest = runs[0] if runs else {}
    run_status = str(latest.get("status") or "")
    status = {
        "running": "active",
        "pending": "active",
        "interrupted": "paused",
        "error": "error",
        "timeout": "error",
    }.get(run_status, "done")
    progress = values.get("progress") or {}
    updated = latest.get("updated_at") or thread.get("updated_at")
    return {
        "id": thread["thread_id"],
        "title": _title(thread, values),
        "status": status,
        "run_status": run_status or "no_runs",
        "stage": progress.get("text") or progress.get("stage") or "",
        "role": progress.get("agent") or "",
        "evidence": len(values.get("evidence") or []),
        "entities": len(values.get("anchors") or values.get("entities") or []),
        "updated_at": updated,
        "last_message": _last_message(values),
        "has_report": bool(values.get("last_report") or values.get("report_result")),
        "run_id": latest.get("run_id") or "",
    }


def _database_data() -> dict:
    if not DATABASE_URL:
        return {"reports": [], "memory": []}
    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT investigation_id, thread_id, task, summary, snapshot, updated_at
                FROM agent_memory.investigations
                ORDER BY updated_at DESC LIMIT 12
            """)
            case_rows = cursor.fetchall()
            cursor.execute("""
                SELECT id, statement, status, source_case_ids, evidence_ids, valid_to, updated_at
                FROM agent_memory.context_facts
                WHERE superseded_by IS NULL AND (valid_to IS NULL OR valid_to > now())
                ORDER BY updated_at DESC LIMIT 6
            """)
            fact_rows = cursor.fetchall()
    reports = []
    for investigation_id, thread_id, task, summary, snapshot, updated_at in case_rows:
        snapshot = snapshot or {}
        artifacts = snapshot.get("artifacts") or []
        last_report = snapshot.get("last_report") or {}
        if not last_report and not artifacts:
            continue
        reports.append({
            "id": investigation_id,
            "thread_id": thread_id or investigation_id,
            "title": last_report.get("title") or task or "Отчёт расследования",
            "summary": summary or "",
            "updated_at": updated_at.isoformat(),
            "files": len(artifacts),
            "status": "ready",
        })
    memory = [{
        "id": fact_id,
        "statement": statement,
        "status": status,
        "source": (source_case_ids or [""])[0],
        "evidence": len(evidence_ids or []),
        "valid_to": valid_to.isoformat() if valid_to else None,
        "updated_at": updated_at.isoformat(),
    } for fact_id, statement, status, source_case_ids, evidence_ids, valid_to, updated_at in fact_rows]
    return {"reports": reports, "memory": memory}


async def _service_health(name: str, url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(url)
        return {"name": name, "ok": response.status_code < 400, "status": response.status_code}
    except httpx.RequestError:
        return {"name": name, "ok": False, "status": 0}


async def dashboard(client: AegraClient) -> dict:
    threads_payload, database = await asyncio.gather(
        client.request("GET", "/threads"),
        asyncio.to_thread(_database_data),
    )
    threads = threads_payload if isinstance(threads_payload, list) else threads_payload.get("threads") or []
    threads = threads[:10]

    async def load(thread):
        try:
            state, runs = await asyncio.gather(client.state(thread["thread_id"]), client.runs(thread["thread_id"]))
            return _thread_view(thread, state, runs)
        except RuntimeError:
            return _thread_view(thread, {}, [])

    views = await asyncio.gather(*(load(thread) for thread in threads))
    views.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    mcp_url = os.environ.get("LOGICORE_MCP_URL") or os.environ.get("LOGIQ_MCP_URL") or "http://logicore-mcp:9000/mcp"
    mcp_health = mcp_url.rstrip("/").removesuffix("/mcp") + "/health"
    s3_url = os.environ.get("ARTIFACT_S3_HEALTH_URL", "http://seaweedfs:9333/cluster/healthz")
    health = await asyncio.gather(
        _service_health("Aegra", f"{client.base_url}/health"),
        _service_health("LogiCore MCP", mcp_health),
        _service_health("PostgreSQL", "http://agent-memory-db:5432"),
        _service_health("S3", s3_url),
    )
    database_health = next(item for item in health if item["name"] == "PostgreSQL")
    database_health["ok"] = bool(DATABASE_URL)
    if DATABASE_URL:
        try:
            await asyncio.to_thread(_database_data)
        except Exception:
            database_health["ok"] = False
    active = [item for item in views if item["status"] == "active"]
    return {
        "health": health,
        "checked_at": datetime.now(UTC).isoformat(),
        "cases": [item for item in views if item["status"] in ("active", "paused", "error")],
        "runs": active,
        "chats": views[:8],
        "reports": database["reports"],
        "memory": database["memory"],
    }
