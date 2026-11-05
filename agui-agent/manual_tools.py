"""Контролируемые read-only MCP-вызовы с обычной Evidence-нормализацией."""

import json
from uuid import uuid4

from pydantic import BaseModel, Field

from aegra_client import AegraClient
from tools import _MCP_TOOLS_BY_NAME, _is_windowed_tool, build_mcp_evidence


class ManualToolCall(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)
    checkpoint_id: str | None = None


def list_manual_tools() -> list[dict]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "schema": tool.args or {},
        }
        for tool in _MCP_TOOLS_BY_NAME.values()
    ]


def _result_text(result) -> str:
    if isinstance(result, str):
        return result
    if hasattr(result, "text"):
        return result.text
    if isinstance(result, list):
        parts = [part.get("text", "") for part in result if isinstance(part, dict) and part.get("type") == "text"]
        if parts:
            return "".join(parts)
    return json.dumps(result, ensure_ascii=False, default=str)


def _data(result) -> dict:
    text = _result_text(result)
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        data = {"text": text}
    return data if isinstance(data, dict) else {"result": data}


async def execute_manual_tool(client: AegraClient, thread_id: str, call: ManualToolCall) -> dict:
    if call.tool not in _MCP_TOOLS_BY_NAME:
        raise ValueError(f"Инструмент {call.tool} не входит в read-only MCP allowlist")
    active = [run for run in await client.runs(thread_id) if run.get("status") in ("pending", "running")]
    if active:
        raise ValueError("Ручной вызов доступен только на паузе")
    state = await client.state(thread_id)
    values = state.get("values") or {}
    context = values.get("context") or {}
    focus = context.get("focus_window") or {}
    args = dict(call.arguments)
    if _is_windowed_tool(call.tool) or "time_from" in args or "time_to" in args:
        if not args.get("time_from"):
            args["time_from"] = focus.get("time_from") or context.get("time_from")
        if not args.get("time_to"):
            args["time_to"] = focus.get("time_to") or context.get("time_to")
    try:
        result = await _MCP_TOOLS_BY_NAME[call.tool].ainvoke(args)
        data = _data(result)
    except Exception as exc:
        data = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    evidence_id = f"manual-{uuid4().hex}"
    evidence = build_mcp_evidence(call.tool, args, data, evidence_id, context.get("id"))
    checkpoint = state.get("checkpoint") or {}
    await client.request("POST", f"/threads/{thread_id}/state", json={
        "values": {"evidence": [evidence]},
        "checkpoint_id": call.checkpoint_id or checkpoint.get("checkpoint_id") or state.get("checkpoint_id"),
    })
    updated = await client.state(thread_id)
    return {**updated, "manual_evidence": evidence}
