import json
from types import SimpleNamespace

from conftest import FakeAgent, FakeStructured, build_graph, invoke
from langchain_core.messages import HumanMessage
from llm import IntentDecision

import memory as memory_mod
from tools import inspect_memory


class OverviewMemory:
    def __init__(self):
        self.queries = []
        self.saved = False

    def recent_cases(self, user_id, limit):
        assert user_id == "anonymous"
        return [{
            "investigation_id": "case-old",
            "task": "Проверка VPN",
            "summary": "Найдена причина",
            "updated_at": "2026-11-03T10:00:00Z",
            "work_mode": "deep",
        }]

    def recent_facts(self, scope, limit):
        return [{
            "id": "fact-vpn",
            "statement": "10.20.0.0/16 используется офисным VPN",
            "status": "confirmed",
            "category": "network",
            "valid_to": None,
        }]

    def search_facts(self, scope, query, limit):
        self.queries.append(query)
        return self.recent_facts(scope, limit)

    def save_case(self, *args):
        self.saved = True
        raise AssertionError("Обзор памяти не должен ничего сохранять")


def decision(query=""):
    return IntentDecision(
        intent="simple",
        rationale="Пользователь просит read-only обзор памяти",
        needs_new_data=False,
        inspect_memory=True,
        memory_query=query,
    )


def test_direct_question_shows_memory_without_agents_or_writes(patch_llm, monkeypatch):
    fake = OverviewMemory()
    monkeypatch.setattr(memory_mod, "memory", fake)
    agent = FakeAgent({})
    patch_llm(agent, FakeStructured(decision()))
    state = {
        "context": {"id": "case-current"},
        "work_mode": "deep",
        "evidence": [{"id": "ev-1"}],
        "findings": [{"claim": "Проверено"}],
        "hypotheses": [],
    }

    result = invoke(build_graph(), "Что у тебя в памяти?", **state)

    answer = result["messages"][-1].content
    assert "case-current" in answer
    assert "офисным VPN" in answer
    assert "Проверка VPN" in answer
    assert "не являются свежими Evidence" in answer
    assert result["progress"]["state"] == "done"
    assert agent.calls == 0
    assert fake.saved is False


def test_specific_memory_question_uses_semantic_search(patch_llm, monkeypatch):
    fake = OverviewMemory()
    monkeypatch.setattr(memory_mod, "memory", fake)
    patch_llm(FakeAgent({}), FakeStructured(decision("VPN")))

    result = invoke(build_graph(), "Что ты помнишь про VPN?")

    assert fake.queries == ["VPN"]
    assert "офисным VPN" in result["messages"][-1].content


def test_inspect_memory_tool_returns_structured_snapshot(monkeypatch):
    fake = OverviewMemory()
    monkeypatch.setattr(memory_mod, "memory", fake)
    runtime = SimpleNamespace(
        state={"messages": [HumanMessage(content="Память")]},
        config={"configurable": {"user_id": "anonymous"}},
    )

    payload = json.loads(inspect_memory.func(limit=3, runtime=runtime))

    assert payload["ok"] is True
    assert payload["current_case"] is None
    assert payload["facts"][0]["id"] == "fact-vpn"
    assert payload["cases"][0]["investigation_id"] == "case-old"
