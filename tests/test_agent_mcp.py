import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "agent-mcp"))
spec = importlib.util.spec_from_file_location(
    "agent_mcp_app",
    Path(__file__).parents[1] / "agent-mcp" / "app.py",
)
agent_mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_mcp)


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload if payload is not None else {}
        self.content = b"{}"
        self.text = "error"

    def json(self):
        return self.payload


def run(coroutine):
    return asyncio.run(coroutine)


def test_start_returns_immediately_with_scoped_id(monkeypatch):
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/threads":
            return Response(201)
        return Response(202, {"run_id": "run-1", "status": "pending"})

    monkeypatch.setattr(agent_mcp, "_request", request)
    result = run(agent_mcp.investigation_start("Проверь ошибки", {"last_minutes": 60}, "deep"))

    assert result["ok"] is True
    assert result["run_id"] == "run-1"
    assert result["investigation_id"].startswith("inv-")
    assert calls[1][0] == "POST"
    assert calls[1][1].endswith("/runs")
    prompt = calls[1][2]["json"]["input"]["messages"][0]["content"]
    assert "last_minutes" in prompt
    assert "режим deep" in prompt


def test_status_and_result_use_current_state(monkeypatch):
    state = {
        "values": {
            "progress": {"stage": "reporter", "state": "running", "message": "Формирую ответ"},
            "work_mode": "deep",
            "report_result": {"answer": "Вывод", "limitations": []},
            "last_report": {"title": "Отчёт", "markdown": "# Вывод"},
            "artifacts": [{
                "storage": "inline",
                "key": "investigations/case/run/report.md",
                "filename": "report.md",
                "data_url": "data:text/markdown;base64,AAAA",
            }],
            "memory_operations": [{"status": "saved"}],
        },
    }

    async def request(method, path, **kwargs):
        return Response(payload=state)

    monkeypatch.setattr(agent_mcp, "_request", request)
    status = run(agent_mcp.investigation_status("inv-" + "a" * 32))
    result = run(agent_mcp.investigation_result("inv-" + "a" * 32))

    assert status["stage"] == "reporter"
    assert result["report"] == "# Вывод"
    assert result["memory_operations"][0]["status"] == "saved"
    assert "data_url" not in result["artifacts"][0]
    assert "url" not in result["artifacts"][0]


def test_result_returns_fresh_s3_link(monkeypatch):
    state = {"values": {
        "last_report": {"markdown": "Отчёт"},
        "artifacts": [{"storage": "s3", "key": "case/report.md", "filename": "report.md"}],
    }}

    async def request(method, path, **kwargs):
        return Response(payload=state)

    monkeypatch.setattr(agent_mcp, "_request", request)
    monkeypatch.setattr(agent_mcp, "artifact_url", lambda item: "https://files.local/signed")
    result = run(agent_mcp.investigation_result("inv-" + "b" * 32))
    assert result["artifacts"][0]["url"] == "https://files.local/signed"


def test_evidence_filter_and_access_isolation(monkeypatch):
    async def request(method, path, **kwargs):
        return Response(payload={"values": {"evidence": [
            {"id": "ev-1", "iql": "action = 'allow'"},
            {"id": "ev-2", "iql": "action = 'deny'"},
        ]}})

    monkeypatch.setattr(agent_mcp, "_request", request)
    result = run(agent_mcp.investigation_evidence("inv-" + "c" * 32, "ev-2"))
    assert result["evidence"] == [{"id": "ev-2", "iql": "action = 'deny'"}]

    rejected = run(agent_mcp.investigation_status("ui-thread"))
    assert rejected["ok"] is False


def test_cancel_uses_active_aegra_run(monkeypatch):
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path))
        if method == "GET":
            return Response(payload=[{"run_id": "run-active", "status": "running"}])
        return Response(202, {})

    monkeypatch.setattr(agent_mcp, "_request", request)
    result = run(agent_mcp.investigation_cancel("inv-" + "d" * 32))
    assert result["status"] == "cancelled"
    assert calls[-1] == (
        "POST",
        "/threads/inv-" + "d" * 32 + "/runs/run-active/cancel",
    )


def test_continue_uses_same_thread_after_cancel(monkeypatch):
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return Response(payload=[{"run_id": "run-old", "status": "cancelled"}])
        return Response(202, {"run_id": "run-new", "status": "pending"})

    monkeypatch.setattr(agent_mcp, "_request", request)
    result = run(agent_mcp.investigation_continue(
        "inv-" + "e" * 32,
        "Проверь выбранный host",
        scope_mode="targeted",
    ))

    assert result["ok"] is True
    assert result["run_id"] == "run-new"
    assert calls[-1][1] == "/threads/inv-" + "e" * 32 + "/runs"
    prompt = calls[-1][2]["json"]["input"]["messages"][0]["content"]
    assert "InvestigationScope=targeted" in prompt


def test_continue_rejects_parallel_run(monkeypatch):
    async def request(method, path, **kwargs):
        return Response(payload=[{"run_id": "run-active", "status": "running"}])

    monkeypatch.setattr(agent_mcp, "_request", request)
    result = run(agent_mcp.investigation_continue("inv-" + "f" * 32, "Продолжи"))

    assert result["ok"] is False
    assert result["error_code"] == "run_active"
    assert result["run_id"] == "run-active"


def test_restart_repeats_last_task_after_error(monkeypatch):
    calls = []

    async def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path.endswith("/state"):
            return Response(payload={"values": {"messages": [
                {"type": "human", "content": "Первый вопрос"},
                {"type": "ai", "content": "Ответ"},
                {"type": "human", "content": "Последний вопрос"},
            ]}})
        if method == "GET":
            return Response(payload=[{"run_id": "run-error", "status": "error"}])
        return Response(202, {"run_id": "run-retry", "status": "pending"})

    monkeypatch.setattr(agent_mcp, "_request", request)
    result = run(agent_mcp.investigation_restart("inv-" + "0" * 32))

    assert result["run_id"] == "run-retry"
    assert calls[-1][2]["json"]["input"]["messages"][0]["content"] == "Последний вопрос"


def test_mcp_tools_expose_typed_output_schemas():
    tools = run(agent_mcp.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert "investigation_continue" in by_name
    schema = by_name["investigation_result"].output_schema
    assert schema["type"] == "object"
    assert {"report", "claims", "scope", "next_directions", "run_metrics"} <= set(schema["properties"])
    start_schema = by_name["investigation_start"].output_schema
    assert "error_code" in start_schema["properties"]


def test_mcp_call_returns_structured_content_not_serialized_json(monkeypatch):
    async def request(method, path, **kwargs):
        return Response(payload={"values": {"evidence": [{"id": "ev-1", "result": {"count": 1}}]}})

    monkeypatch.setattr(agent_mcp, "_request", request)
    response = run(agent_mcp.mcp.call_tool(
        "investigation_evidence",
        {"investigation_id": "inv-" + "1" * 32},
    ))
    structured = response.get("structuredContent") if isinstance(response, dict) else response.structured_content

    assert structured["ok"] is True
    assert structured["evidence"][0]["id"] == "ev-1"
