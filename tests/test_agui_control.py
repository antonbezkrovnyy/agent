import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agui-agent"))

from control import _update_plan, _update_prompt, _update_task
from control_overrides import effective_prompt, with_prompt_override
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def item(item_id, **kwargs):
    return {
        "id": item_id,
        "goal": f"Проверка {item_id}",
        "acceptance": "Есть Evidence",
        "required": True,
        "depends_on": [],
        "status": "pending",
        **kwargs,
    }


def test_plan_edit_preserves_unedited_items_and_validates_schema():
    values = {
        "investigation_plan": {"goal": "План", "version": 1},
        "work_items": [item("a"), item("b", required=False, depends_on=["a"])],
    }
    result = _update_plan(values, [{"id": "a", "goal": "Новая цель", "acceptance": "Новый критерий"}])
    assert len(result["work_items"]) == 2
    assert result["work_items"][0]["goal"] == "Новая цель"
    assert result["work_items"][1]["depends_on"] == ["a"]
    assert result["investigation_plan"]["items"][0]["acceptance"] == "Новый критерий"


def test_plan_edit_rejects_completed_items_and_cycles():
    with pytest.raises(ValueError, match="выполненный"):
        _update_plan({"work_items": [item("a", status="done")]}, [{"id": "a", "goal": "Другая цель"}])
    with pytest.raises(ValueError, match="Циклическая"):
        _update_plan(
            {"work_items": [item("a", required=False), item("b", required=False)]},
            [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}],
        )


def test_task_and_prompt_edits_are_scoped():
    task = _update_task(
        {"messages": [{"id": "u1", "type": "human", "content": "Старая задача"}]},
        "Новая задача",
    )
    assert task["messages"][0] == {"id": "u1", "type": "human", "content": "Новая задача"}
    prompt = _update_prompt({"control_overrides": {"scout": {"prompt": "old"}}}, "planner", "new prompt")
    assert prompt["control_overrides"]["planner"]["prompt"] == "new prompt"
    assert prompt["control_overrides"]["scout"]["prompt"] == "old"


def test_prompt_override_changes_only_selected_role():
    state = {"control_overrides": {"planner": {"prompt": "override"}}}
    messages = [SystemMessage(content="base"), HumanMessage(content="task")]
    overridden = with_prompt_override(state, "planner", messages)
    assert overridden[0].content == "override"
    assert overridden[1].content == "task"
    assert effective_prompt(state, "scout", "base") == "base"


def test_manual_tool_creates_real_evidence_record():
    from manual_tools import ManualToolCall, execute_manual_tool

    class FakeClient:
        def __init__(self):
            self.updates = []

        async def runs(self, thread_id):
            return []

        async def state(self, thread_id):
            return {"checkpoint": {"checkpoint_id": "c1"}, "values": {"context": {"id": "case-1"}}}

        async def request(self, method, path, **kwargs):
            self.updates.append(kwargs["json"])
            return {}

    client = FakeClient()
    result = asyncio.run(execute_manual_tool(client, "thread-1", ManualToolCall(tool="server_time")))
    evidence = result["manual_evidence"]
    assert evidence["tool"] == "server_time"
    assert evidence["id"].startswith("manual-")
    assert evidence["investigation_id"] == "case-1"
    assert evidence["status"] == "ok"
    assert client.updates[0]["values"]["evidence"] == [evidence]


def test_manual_tool_rejects_unknown_tool():
    from manual_tools import ManualToolCall, execute_manual_tool

    with pytest.raises(ValueError, match="allowlist"):
        asyncio.run(execute_manual_tool(None, "thread-1", ManualToolCall(tool="shell")))


def test_tool_step_mode_is_opt_in(monkeypatch):
    from step_mode import ToolStepMiddleware

    calls = [{"id": "call-1", "name": "count_events", "args": {"last_minutes": 60}}]
    state = {"messages": [AIMessage(content="", tool_calls=calls)], "control_overrides": {}}
    middleware = ToolStepMiddleware("scout")
    assert middleware.after_model(state, None) is None
    paused = []
    monkeypatch.setattr("step_mode.interrupt", lambda payload: paused.append(payload))
    state["control_overrides"] = {"_step": {"tools": True}}
    middleware.after_model(state, None)
    assert paused[0]["type"] == "tool_calls_ready"
    assert paused[0]["tool_calls"] == calls


def test_agent_input_preserves_control_overrides():
    from agents import _agent_input

    overrides = {"_step": {"tools": True}, "scout": {"prompt": "override"}}
    result = _agent_input({"messages": [], "control_overrides": overrides}, "scout_msgs")
    assert result["control_overrides"] == overrides
