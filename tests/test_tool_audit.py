import asyncio
import json

import pytest
from langchain_core.messages import ToolMessage

from reporting import build_investigation_package
from tools import normalize_tool_result
from test_investigation_package import state_with_evidence, unzip


class Request:
    def __init__(self):
        self.tool_call = {"name": "log_finding", "args": {"claim": "test"}, "id": "call-internal"}
        self.state = {
            "context": {"id": "case-audit"},
            "active_work_item": {"id": "item-1"},
            "mcp_reuse_run_id": "run-1",
            "progress": {"agent": "investigator"},
        }

    def override(self, tool_call):
        self.tool_call = tool_call
        return self


def test_internal_tool_call_is_recorded_with_result_and_context():
    async def handler(request):
        return ToolMessage(
            content=json.dumps({"ok": True, "saved": True}),
            tool_call_id="call-internal",
            name="log_finding",
        )

    result = asyncio.run(normalize_tool_result.awrap_tool_call(Request(), handler))
    record = result.update["tool_calls"][0]

    assert record["id"] == "call-internal"
    assert record["investigation_id"] == "case-audit"
    assert record["run_id"] == "run-1"
    assert record["role"] == "investigator"
    assert record["work_item_id"] == "item-1"
    assert record["arguments_requested"] == {"claim": "test"}
    assert record["arguments_effective"] == {"claim": "test"}
    assert record["result"] == {"ok": True, "saved": True}
    assert record["status"] == "ok"


def test_package_contains_every_tool_call_and_flags_missing_evidence_result():
    state = state_with_evidence()
    state["tool_calls"] = [{
        "id": "call-1",
        "tool": "search_events",
        "arguments_requested": {},
        "arguments_effective": {"time_from": "2026-01-01T00:00:00Z"},
        "status": "ok",
        "result": {"ok": True},
        "evidence_id": "ev-search",
        "reused": False,
    }, {
        "id": "call-2",
        "tool": "search_events",
        "arguments_requested": {},
        "arguments_effective": {},
        "status": "ok",
        "result": {"ok": True, "reused": True},
        "evidence_id": "ev-search",
        "reused": True,
        "reused_from": "ev-search",
    }]

    _, url, _ = build_investigation_package("Аудит", "# Вывод", state)
    with unzip(url) as bundle:
        calls = [json.loads(line) for line in bundle.read("tool_calls.jsonl").decode().splitlines()]
        manifest = json.loads(bundle.read("manifest.json"))

    assert [item["id"] for item in calls] == ["call-1", "call-2"]
    assert manifest["tool_call_count"] == 2
    assert manifest["reused_tool_call_count"] == 1
    assert manifest["tool_call_index"] == {"call-1": 1, "call-2": 2}
    assert manifest["package_validation"]["ok"] is True

    del state["evidence"][0]["result"]
    _, url, _ = build_investigation_package("Аудит", "# Вывод", state)
    with unzip(url) as bundle:
        validation = json.loads(bundle.read("manifest.json"))["package_validation"]
    assert validation["ok"] is False
    assert validation["self_contained"] is False
    assert validation["missing_result_evidence_ids"] == ["ev-search"]


def test_internal_reuse_is_recorded_from_tool_result():
    async def handler(request):
        return ToolMessage(
            content=json.dumps({
                "ok": True,
                "evidence_id": "sql-first",
                "reused": True,
                "reused_from": "sql-first",
            }),
            tool_call_id="call-internal",
            name="analysis_sql",
        )

    result = asyncio.run(normalize_tool_result.awrap_tool_call(Request(), handler))
    record = result.update["tool_calls"][0]

    assert record["reused"] is True
    assert record["reused_from"] == "sql-first"
    assert record["evidence_id"] == "sql-first"


def test_cancelled_tool_call_is_emitted_before_reraise(monkeypatch):
    import asyncio as asyncio_module

    events = []
    monkeypatch.setattr("tools._emit_tool_update", lambda request, result: events.append(result))

    async def handler(request):
        raise asyncio_module.CancelledError("test cancel")

    with pytest.raises(asyncio_module.CancelledError):
        asyncio.run(normalize_tool_result.awrap_tool_call(Request(), handler))

    record = events[0].update["tool_calls"][0]
    assert record["status"] == "cancelled"
    assert record["result"]["cancelled"] is True
    assert record["id"] == "call-internal"


def test_cancelled_mcp_call_is_emitted_before_reraise(monkeypatch):
    import asyncio as asyncio_module

    events = []
    monkeypatch.setattr("tools._emit_tool_update", lambda request, result: events.append(result))
    request = Request()
    request.tool_call["name"] = "count_events"
    request.state["context"].update({
        "time_from": "2026-01-01T00:00:00Z",
        "time_to": "2026-01-01T01:00:00Z",
    })

    async def handler(request):
        raise asyncio_module.CancelledError("test cancel")

    with pytest.raises(asyncio_module.CancelledError):
        asyncio.run(normalize_tool_result.awrap_tool_call(request, handler))

    record = events[0].update["tool_calls"][0]
    assert record["status"] == "cancelled"
    assert record["arguments_effective"]["time_from"] == "2026-01-01T00:00:00Z"
