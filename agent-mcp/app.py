"""Асинхронный MCP-контракт расследований поверх Aegra runs API."""

import os
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import httpx
from artifact_store import artifact_url
from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

AEGRA_API_URL = os.environ.get("AEGRA_API_URL", "http://aegra:2026").rstrip("/")
AEGRA_ASSISTANT = os.environ.get("AEGRA_ASSISTANT", "agent")
MCP_TOKEN = os.environ.get("AGENT_MCP_TOKEN", "")
THREAD_PREFIX = "inv-"

mcp = MCPServer("logicore-investigations")


def _thread_id(investigation_id: str) -> str:
    if not investigation_id.startswith(THREAD_PREFIX) or len(investigation_id) != len(THREAD_PREFIX) + 32:
        raise ValueError("Неизвестный investigation_id: доступны только запуски через agent-mcp")
    suffix = investigation_id.removeprefix(THREAD_PREFIX)
    if any(char not in "0123456789abcdef" for char in suffix):
        raise ValueError("Некорректный investigation_id")
    return investigation_id


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.request(method, f"{AEGRA_API_URL}{path}", **kwargs)


def _error(response: httpx.Response) -> dict:
    return {
        "ok": False,
        "error": f"Aegra API: {response.status_code}",
        "details": response.text[:500],
    }


def _window_text(window: dict | None) -> str:
    if not window:
        return ""
    allowed = {key: window[key] for key in ("time_from", "time_to", "last_minutes") if key in window}
    return f"\nЗаданное пользователем окно: {allowed}." if allowed else ""


@mcp.tool()
async def investigation_start(
    task: str,
    window: dict[str, Any] | None = None,
    mode: Literal["auto", "quick", "deep"] = "auto",
) -> dict:
    """Запустить расследование и сразу вернуть investigation_id, не ожидая отчёта."""
    if not task.strip():
        return {"ok": False, "error": "task не должен быть пустым"}
    investigation_id = THREAD_PREFIX + uuid4().hex
    prompt = task.strip() + _window_text(window)
    if mode != "auto":
        prompt += f"\nПользователь явно выбрал режим {mode}."
    created = await _request("POST", "/threads", json={
        "threadId": investigation_id,
        "ifExists": "raise",
        "metadata": {"kind": "agent-mcp", "created_at": datetime.now(UTC).isoformat()},
    })
    if created.status_code not in (200, 201):
        return _error(created)
    run = await _request("POST", f"/threads/{investigation_id}/runs", json={
        "assistant_id": AEGRA_ASSISTANT,
        "input": {"messages": [{"role": "human", "content": prompt}]},
        "config": {"configurable": {"user_id": "agent-mcp"}},
        "on_disconnect": "continue",
    })
    if run.status_code not in (200, 201, 202):
        return _error(run)
    payload = run.json() if run.content else {}
    return {
        "ok": True,
        "investigation_id": investigation_id,
        "run_id": payload.get("run_id") or payload.get("runId"),
        "status": payload.get("status") or "pending",
    }


async def _state(investigation_id: str) -> tuple[dict, dict]:
    thread_id = _thread_id(investigation_id)
    response = await _request("GET", f"/threads/{thread_id}/state")
    if response.status_code != 200:
        return {}, _error(response)
    payload = response.json()
    return payload.get("values") or payload, {}


@mcp.tool()
async def investigation_status(investigation_id: str) -> dict:
    """Получить стадию, прогресс, режим и ошибки без блокировки."""
    try:
        values, error = await _state(investigation_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if error:
        return error
    progress = values.get("progress") or {}
    runs = await _request("GET", f"/threads/{investigation_id}/runs")
    run_items = runs.json() if runs.status_code == 200 else []
    if isinstance(run_items, dict):
        run_items = run_items.get("runs") or []
    latest = run_items[0] if run_items else {}
    return {
        "ok": True,
        "investigation_id": investigation_id,
        "stage": progress.get("stage") or "unknown",
        "status": latest.get("status") or progress.get("state") or "unknown",
        "message": progress.get("text") or "",
        "tool_calls": progress.get("tool_calls") or 0,
        "tool_limit": progress.get("tool_limit") or 0,
        "work_mode": values.get("work_mode") or "",
        "memory_error": values.get("memory_error") or "",
        "complete": bool(values.get("report_result") or values.get("last_report")),
    }


@mcp.tool()
async def investigation_result(investigation_id: str) -> dict:
    """Получить отчёт и свежие S3-ссылки на Markdown и ZIP-пакет."""
    try:
        values, error = await _state(investigation_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if error:
        return error
    report = values.get("last_report") or {}
    result = values.get("report_result") or {}
    if not report and not result:
        return {
            "ok": False,
            "error": "Расследование ещё не завершено",
            "investigation_id": investigation_id,
            "progress": values.get("progress") or {},
        }
    artifacts = []
    for item in values.get("artifacts") or []:
        if isinstance(item, dict):
            public = {key: value for key, value in item.items() if key != "data_url"}
            if item.get("storage") == "s3":
                public["url"] = artifact_url(item)
            artifacts.append(public)
    return {
        "ok": True,
        "investigation_id": investigation_id,
        "title": report.get("title") or "Расследование",
        "report": report.get("markdown") or result.get("answer") or "",
        "limitations": result.get("limitations") or [],
        "memory_operations": values.get("memory_operations") or [],
        "artifacts": artifacts,
    }


@mcp.tool()
async def investigation_evidence(investigation_id: str, evidence_id: str | None = None) -> dict:
    """Получить все Evidence или одну полную запись с фактическим IQL и результатом."""
    try:
        values, error = await _state(investigation_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if error:
        return error
    evidence = values.get("evidence") or []
    if evidence_id is not None:
        evidence = [item for item in evidence if item.get("id") == evidence_id]
        if not evidence:
            return {"ok": False, "error": f"Evidence {evidence_id} не найден"}
    return {"ok": True, "investigation_id": investigation_id, "evidence": evidence}


@mcp.tool()
async def investigation_cancel(investigation_id: str) -> dict:
    """Остановить активный run через Aegra; состояние и Evidence сохраняются."""
    try:
        thread_id = _thread_id(investigation_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    runs = await _request("GET", f"/threads/{thread_id}/runs")
    if runs.status_code != 200:
        return _error(runs)
    payload = runs.json()
    items = payload if isinstance(payload, list) else payload.get("runs") or []
    active = next((item for item in items if item.get("status") in ("pending", "running")), None)
    if active is None:
        return {"ok": True, "investigation_id": investigation_id, "status": "not_running"}
    run_id = active.get("run_id") or active.get("runId")
    response = await _request("POST", f"/threads/{thread_id}/runs/{run_id}/cancel", json={"action": "interrupt"})
    if response.status_code not in (200, 202, 204):
        return _error(response)
    return {"ok": True, "investigation_id": investigation_id, "run_id": run_id, "status": "cancelled"}


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "agent-mcp"})


class BearerAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") != "/health":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization") != f"Bearer {MCP_TOKEN}".encode():
                response = JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def run() -> None:
    import uvicorn

    app = mcp.streamable_http_app(streamable_http_path="/mcp", host="0.0.0.0")
    if MCP_TOKEN:
        app = BearerAuthMiddleware(app)
    uvicorn.run(app, host="0.0.0.0", port=9100)


if __name__ == "__main__":
    run()
