"""Опциональная ручная пауза перед tool calls без изменения автопилота."""

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.types import interrupt


class ToolStepMiddleware(AgentMiddleware):
    def __init__(self, role: str):
        self.role = role

    def after_model(self, state: dict, runtime: Any) -> dict | None:
        step = (state.get("control_overrides") or {}).get("_step") or {}
        if not step.get("tools"):
            return None
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        calls = getattr(last, "tool_calls", None) or []
        if not calls:
            return None
        interrupt({
            "type": "tool_calls_ready",
            "role": self.role,
            "work_item_id": str((state.get("active_work_item") or {}).get("id") or ""),
            "tool_calls": [
                {"id": call.get("id"), "name": call.get("name"), "args": call.get("args") or {}}
                for call in calls
            ],
        })
        return None
