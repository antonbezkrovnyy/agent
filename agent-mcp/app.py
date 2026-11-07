"""Асинхронный MCP-контракт расследований поверх Aegra runs API."""

import json
import os
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import httpx
from artifact_store import artifact_url
from investigation_mcp_types import (
    CancelResult,
    EvidenceResult,
    ResultResponse,
    StartResult,
    StatusResult,
)
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
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.request(method, f"{AEGRA_API_URL}{path}", **kwargs)
    except httpx.RequestError as exc:
        return httpx.Response(503, text=f"{type(exc).__name__}: {exc}")


def _failure(code: str, message: str, *, retryable: bool = False, **fields) -> dict:
    return {
        "ok": False,
        "error": message,
        "error_code": code,
        "retryable": retryable,
        **fields,
    }


def _error(response: httpx.Response) -> dict:
    unavailable = response.status_code in (502, 503, 504)
    return _failure(
        "aegra_unavailable" if unavailable else f"aegra_http_{response.status_code}",
        f"Aegra API: {response.status_code}",
        retryable=unavailable,
        details=response.text[:500],
    )


def _window_text(window: dict | None) -> str:
    if not window:
        return ""
    allowed = {key: window[key] for key in ("time_from", "time_to", "last_minutes") if key in window}
    return f"\nЗаданное пользователем окно: {allowed}." if allowed else ""


def _prompt(task: str, window: dict | None, mode: str, scope_mode: str, alert: dict | None) -> str:
    prompt = task.strip() + _window_text(window)
    if mode != "auto":
        prompt += f"\nПользователь явно выбрал режим {mode}."
    if scope_mode != "auto":
        prompt += f"\nПользователь явно выбрал InvestigationScope={scope_mode}."
    if alert:
        prompt += "\nSIEM alert payload:\n" + json.dumps(alert, ensure_ascii=False, default=str)
    return prompt


async def _runs(thread_id: str) -> tuple[list[dict], dict]:
    response = await _request("GET", f"/threads/{thread_id}/runs")
    if response.status_code != 200:
        return [], _error(response)
    payload = response.json()
    items = payload if isinstance(payload, list) else payload.get("runs") or []
    return items, {}


async def _start_run(thread_id: str, prompt: str) -> dict:
    response = await _request("POST", f"/threads/{thread_id}/runs", json={
        "assistant_id": AEGRA_ASSISTANT,
        "input": {"messages": [{"role": "human", "content": prompt}]},
        "config": {"configurable": {"user_id": "agent-mcp"}},
        "on_disconnect": "continue",
    })
    if response.status_code not in (200, 201, 202):
        return _error(response)
    payload = response.json() if response.content else {}
    return {
        "ok": True,
        "investigation_id": thread_id,
        "run_id": payload.get("run_id") or payload.get("runId"),
        "status": payload.get("status") or "pending",
    }


@mcp.tool(structured_output=True)
async def investigation_start(
    task: str,
    window: dict[str, Any] | None = None,
    mode: Literal["auto", "quick", "deep"] = "auto",
    scope_mode: Literal["auto", "discovery", "targeted", "alert"] = "auto",
    alert: dict[str, Any] | None = None,
) -> StartResult:
    """Запустить расследование и сразу вернуть investigation_id, не ожидая отчёта."""
    if not task.strip():
        return _failure("invalid_task", "task не должен быть пустым")
    investigation_id = THREAD_PREFIX + uuid4().hex
    created = await _request("POST", "/threads", json={
        "threadId": investigation_id,
        "ifExists": "raise",
        "metadata": {"kind": "agent-mcp", "created_at": datetime.now(UTC).isoformat()},
    })
    if created.status_code not in (200, 201):
        return _error(created)
    result = await _start_run(investigation_id, _prompt(task, window, mode, scope_mode, alert))
    if not result.get("ok"):
        result["investigation_id"] = investigation_id
    return result


async def _state(investigation_id: str) -> tuple[dict, dict]:
    thread_id = _thread_id(investigation_id)
    response = await _request("GET", f"/threads/{thread_id}/state")
    if response.status_code != 200:
        return {}, _error(response)
    payload = response.json()
    return payload.get("values") or payload, {}


@mcp.tool(structured_output=True)
async def investigation_status(investigation_id: str) -> StatusResult:
    """Получить стадию, прогресс, режим и ошибки без блокировки."""
    try:
        values, error = await _state(investigation_id)
    except ValueError as exc:
        return _failure("invalid_investigation_id", str(exc))
    if error:
        return error
    run_items, error = await _runs(investigation_id)
    if error:
        return error
    latest = run_items[0] if run_items else {}
    progress = values.get("progress") or {}
    return {
        "ok": True,
        "investigation_id": investigation_id,
        "run_id": latest.get("run_id") or latest.get("runId"),
        "stage": progress.get("stage") or "unknown",
        "status": latest.get("status") or progress.get("state") or "unknown",
        "message": progress.get("text") or "",
        "tool_calls": progress.get("tool_calls") or 0,
        "tool_limit": progress.get("tool_limit") or 0,
        "work_mode": values.get("work_mode") or "",
        "scope": values.get("investigation_scope") or {},
        "objective": progress.get("objective") or "",
        "completed_items": progress.get("completed_items") or 0,
        "total_items": progress.get("total_items") or 0,
        "plan_version": (values.get("investigation_plan") or {}).get("version") or 0,
        "memory_error": values.get("memory_error") or "",
        "complete": bool(values.get("report_result") or values.get("last_report")),
    }


@mcp.tool(structured_output=True)
async def investigation_continue(
    investigation_id: str,
    message: str,
    window: dict[str, Any] | None = None,
    mode: Literal["auto", "quick", "deep"] = "auto",
    scope_mode: Literal["auto", "discovery", "targeted", "alert"] = "auto",
    alert: dict[str, Any] | None = None,
) -> StartResult:
    """Продолжить существующее дело тем же тредом после завершения, отмены или ошибки."""
    try:
        _thread_id(investigation_id)
    except ValueError as exc:
        return _failure("invalid_investigation_id", str(exc))
    if not message.strip():
        return _failure("invalid_task", "message не должен быть пустым", investigation_id=investigation_id)
    items, error = await _runs(investigation_id)
    if error:
        return error
    active = next((item for item in items if item.get("status") in ("pending", "running")), None)
    if active:
        return _failure(
            "run_active",
            "Нельзя запустить continuation параллельно с активным run",
            investigation_id=investigation_id,
            run_id=active.get("run_id") or active.get("runId"),
            status=active.get("status") or "running",
        )
    return await _start_run(investigation_id, _prompt(message, window, mode, scope_mode, alert))


@mcp.tool(structured_output=True)
async def investigation_restart(investigation_id: str) -> StartResult:
    """Повторить последний пользовательский запрос после отмены или ошибки в том же треде."""
    try:
        values, error = await _state(investigation_id)
    except ValueError as exc:
        return _failure("invalid_investigation_id", str(exc))
    if error:
        return error
    items, error = await _runs(investigation_id)
    if error:
        return error
    active = next((item for item in items if item.get("status") in ("pending", "running")), None)
    if active:
        return _failure("run_active", "Активный run нельзя перезапустить", investigation_id=investigation_id)
    messages = values.get("messages") or []
    prompt = ""
    for item in reversed(messages):
        kind = item.get("type") or item.get("role") if isinstance(item, dict) else getattr(item, "type", "")
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", "")
        if kind in ("human", "user") and isinstance(content, str):
            prompt = content
            break
    if not prompt:
        return _failure("no_task", "В состоянии треда нет пользовательского запроса для повторного запуска")
    return await _start_run(investigation_id, prompt)


@mcp.tool(structured_output=True)
async def investigation_result(investigation_id: str) -> ResultResponse:
    """Получить отчёт и свежие S3-ссылки на Markdown и ZIP-пакет."""
    try:
        values, error = await _state(investigation_id)
    except ValueError as exc:
        return _failure("invalid_investigation_id", str(exc))
    if error:
        return error
    report = values.get("last_report") or {}
    result = values.get("report_result") or {}
    if not report and not result:
        run_items, run_error = await _runs(investigation_id)
        latest = run_items[0] if run_items else {}
        return _failure(
            "result_not_ready",
            "Расследование ещё не завершено",
            investigation_id=investigation_id,
            status=latest.get("status") or "unknown",
            progress=values.get("progress") or {},
            **({"details": run_error["error"]} if run_error else {}),
        )
    artifacts = []
    for item in values.get("artifacts") or []:
        if isinstance(item, dict):
            public = {key: value for key, value in item.items() if key != "data_url"}
            if item.get("storage") == "s3":
                public["url"] = artifact_url(item)
            artifacts.append(public)
    scope = values.get("investigation_scope") or {}
    return {
        "ok": True,
        "investigation_id": investigation_id,
        "status": "success",
        "title": report.get("title") or "Расследование",
        "report": report.get("markdown") or result.get("answer") or "",
        "claims": result.get("claims") or [],
        "limitations": result.get("limitations") or [],
        "not_checked": result.get("not_checked") or [],
        "scope": scope,
        "next_directions": scope.get("optional") or [],
        "memory_operations": values.get("memory_operations") or [],
        "run_metrics": values.get("run_metrics") or [],
        "artifacts": artifacts,
        "verification": report.get("verification") or result.get("verification") or {},
        "tool_calls": values.get("tool_calls") or [],
        "analysis_datasets": values.get("analysis_datasets") or [],
        "analysis_results": values.get("analysis_results") or [],
        "package_sha256": report.get("package_sha256") or "",
        "manifest_sha256": report.get("manifest_sha256") or "",
    }


@mcp.tool(structured_output=True)
async def investigation_evidence(investigation_id: str, evidence_id: str | None = None) -> EvidenceResult:
    """Получить все Evidence или одну полную запись с фактическим IQL и результатом."""
    try:
        values, error = await _state(investigation_id)
    except ValueError as exc:
        return _failure("invalid_investigation_id", str(exc))
    if error:
        return error
    evidence = values.get("evidence") or []
    if evidence_id is not None:
        evidence = [item for item in evidence if item.get("id") == evidence_id]
        if not evidence:
            return _failure("evidence_not_found", f"Evidence {evidence_id} не найден")
    return {"ok": True, "investigation_id": investigation_id, "evidence": evidence}


@mcp.tool(structured_output=True)
async def investigation_cancel(investigation_id: str) -> CancelResult:
    """Остановить активный run через Aegra; состояние и Evidence сохраняются."""
    try:
        thread_id = _thread_id(investigation_id)
    except ValueError as exc:
        return _failure("invalid_investigation_id", str(exc))
    items, error = await _runs(thread_id)
    if error:
        return error
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
