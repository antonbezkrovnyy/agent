import json
from types import SimpleNamespace

from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc

import memory as memory_mod
from tools import search_similar_cases


class FakeMemory:
    enabled = True

    def __init__(self, cases):
        self.cases_to_find = list(cases)
        self.cases = {}
        self.excluded = []

    def recent_cases(self, user_id, limit):
        return []

    def save_case(self, user_id, case):
        self.cases[(user_id, case["investigation_id"])] = case

    def search_facts(self, scope, query, limit):
        return []

    def search_cases(self, user_id, query, limit, exclude_id=""):
        self.excluded.append(exclude_id)
        return [dict(item) for item in self.cases_to_find[:limit]]


def investigation_agent():
    return FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [ai("Данные проверены")],
        "Ты — репортёр": [ai("Ответ")],
    })


def similar_case():
    return {
        "investigation_id": "case-similar",
        "task": "Похожий сбой srv-auth-01",
        "summary": "Причиной был deploy",
        "signature": "Ошибки auth после deploy",
        "entities": [{"type": "host", "value": "srv-auth-01"}],
        "work_mode": "deep",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "similarity": 0.82,
    }


def test_similar_case_is_context_not_continuation(patch_llm, monkeypatch):
    fake = FakeMemory([similar_case()])
    monkeypatch.setattr(memory_mod, "memory", fake)
    agent = investigation_agent()
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "Проверь новый сбой srv-auth-01")

    assert result["similar_cases"][0]["investigation_id"] == "case-similar"
    assert result["memory_loaded"] == ""
    assert result["context"]["id"] != "case-similar"
    assert all(item["investigation_id"] != "case-similar" for item in result["evidence"])
    assert any("Похожий сбой srv-auth-01" in system for system in agent.systems)


def test_similar_case_tool_returns_compact_cards(monkeypatch):
    fake = FakeMemory([similar_case()])
    monkeypatch.setattr(memory_mod, "memory", fake)
    runtime = SimpleNamespace(
        state={"context": {"id": "case-current"}},
        config={"configurable": {"user_id": "anonymous"}},
    )

    payload = json.loads(search_similar_cases.func("сбой srv-auth-01", runtime=runtime))

    assert payload["ok"] is True
    assert payload["cases"][0]["investigation_id"] == "case-similar"
    assert "snapshot" not in payload["cases"][0]
    assert fake.excluded == ["case-current"]
