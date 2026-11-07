import asyncio

from conftest import FakeAgent, FakeStructured, agents_mod, intent
from config import Settings
from contracts import InvestigationPlan, InvestigationScope, WorkItem
from investigation import node_intent
from investigation_graph import build_investigation_graph
from test_agent_runtime import runtime_state


def test_expired_role_does_not_call_model(patch_llm):
    agent = FakeAgent({})
    patch_llm(agent, FakeStructured(intent()))
    state = {
        **runtime_state(),
        "run_deadline_at": "2000-01-01T00:00:00+00:00",
    }

    result, _, _ = asyncio.run(agents_mod._run_agent("investigator", state, "inv_msgs"))

    assert agent.calls == 0
    assert result["termination_reason"] == "run_timeout"
    assert result["structured_response"].status == "partial"


def test_expired_deep_marks_pending_items_skipped():
    scope = InvestigationScope(objective="Проверить события", included=["Проверить события"])
    item = WorkItem(id="main", goal="Проверить события")
    state = {
        "messages": [],
        "context": {"id": "case-deadline"},
        "run_deadline_at": "2000-01-01T00:00:00+00:00",
        "investigation_scope": scope.model_dump(),
        "investigation_plan": InvestigationPlan(goal=scope.objective, items=[item]).model_dump(),
        "work_items": [item.model_dump()],
        "active_work_item": {},
        "replan_requested": False,
        "rework": 0,
    }

    result = asyncio.run(build_investigation_graph(Settings()).ainvoke(state))

    assert result["work_items"][0]["status"] == "skipped"
    assert "Проверить события" in result["inv_result"]["not_checked"]
    assert "Истёк общий дедлайн run." in result["inv_result"]["limitations"]


def test_low_confidence_new_topic_preserves_current_case(patch_llm):
    decision = intent("complex", new=True, mode="deep")
    decision.confidence = 0.1
    patch_llm(FakeAgent({}), FakeStructured(decision))
    state = {
        "messages": [],
        "context": {"id": "case-current"},
        "evidence": [{"id": "ev-current"}],
        "last_report": {"title": "Текущее дело"},
    }

    update = node_intent(state)

    assert update["transition"] == "continue"
    assert "confidence_guard" in update["intent"]
    assert "evidence" not in update


def test_confident_new_topic_is_allowed(patch_llm):
    decision = intent("complex", new=True, mode="deep")
    decision.confidence = 0.95
    patch_llm(FakeAgent({}), FakeStructured(decision))
    state = {
        "messages": [],
        "context": {"id": "case-current"},
        "evidence": [{"id": "ev-current"}],
        "last_report": {"title": "Текущее дело"},
    }

    update = node_intent(state)

    assert update["transition"] == "new"
    assert "confidence_guard" not in update["intent"]
