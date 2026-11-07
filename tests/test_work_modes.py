import pytest

from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc
from tools import SCOUT_MCP_TOOL_NAMES, scout_tools


@pytest.mark.parametrize("query", [
    "Сравни количество и распределение событий за вчера и сегодня",
    "Поступают ли данные от источника saturn-1 за последний час?",
    "Какова доля deny и средняя интенсивность событий за сутки?",
])
def test_statistical_requests_route_to_scout(patch_llm, query):
    agent = FakeAgent({
        "Ты — скаут": [
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("ScoutResult", {
                "note": "Статистика готова",
                "signals": [],
                "evidence_ids": [],
                "escalation": {"needed": False, "reason": "", "suggested_skill": None},
            })]),
        ],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), query)

    assert result["work_mode"] == "quick"
    assert result["scout_result"]["note"] == "Статистика готова"
    assert result["inv_msgs"] == []


def test_scout_tools_exclude_full_export_and_memory_overview():
    names = {item.name for item in scout_tools}

    assert "count_events" in names
    assert "search_events" in names
    assert "extract_data" not in names
    assert "inspect_memory" not in names
    assert names <= SCOUT_MCP_TOOL_NAMES | {
        "log_finding",
        "search_context_memory",
        "search_similar_cases",
        "wiki_search",
        "wiki_read",
    }


def test_quick_goes_directly_to_reporter(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("ScoutResult", {
                "note": "17 событий",
                "signals": [],
                "evidence_ids": [],
                "escalation": {"needed": False, "reason": "", "suggested_skill": None},
            })]),
        ],
        "Ты — репортёр": [ai("За час 17 событий")],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "что в логикоре за час")

    assert result["work_mode"] == "quick"
    assert result.get("inv_summary", "") == ""
    assert result["scout_result"]["escalation"]["needed"] is False
    assert result["report_result"]["answer"] == "За час 17 событий"
    scout_messages = [message for message in result["messages"] if message.name == "scout"]
    assert len(scout_messages) == 1
    assert "17 событий" in scout_messages[0].content
    assert not scout_messages[0].tool_calls


def test_deep_skips_scout(patch_llm):
    agent = FakeAgent({
        "Ты — инвестигейтор": [ai("Нашёл первое событие")],
        "Ты — репортёр": [ai("Сбой начался вчера")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")))

    result = invoke(build_graph(), "найди когда начался сбой")

    assert result["work_mode"] == "deep"
    assert result["scout_msgs"] == []
    assert result["inv_summary"] == "Нашёл первое событие"


def test_quick_escalation_preserves_investigation(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("Есть зацепка")],
        "Ты — инвестигейтор": [ai("Проверил раннее окно")],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "проверь события")

    assert result["work_mode"] == "deep"
    assert result["depth_history"][-1]["from"] == "quick"
    assert result["depth_history"][-1]["to"] == "deep"
    assert len(result["evidence"]) == 1
    scout_message = next(message for message in result["messages"] if message.name == "scout")
    assert "- **Следующий этап:** глубокое расследование" in scout_message.content


def test_update_window_controls_following_queries(patch_llm):
    agent = FakeAgent({
        "Ты — инвестигейтор": [
            ai(calls=[tc("update_window", {
                "action": "replace",
                "time_from": "2026-01-01T00:00:00Z",
                "time_to": "2026-01-01T02:00:00Z",
                "reason": "Проверяю первое появление",
            })]),
            ai(calls=[tc("count_events", {})]),
            ai("Проверил раннее окно"),
        ],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")))

    result = invoke(build_graph(), "найди первое появление")
    count = next(item for item in result["evidence"] if item["tool"] == "count_events")

    assert result["context"]["time_from"] == "2026-01-01T00:00:00+00:00"
    assert result["context"]["time_to"] == "2026-01-01T02:00:00+00:00"
    assert result["context"]["window_history"][-1]["action"] == "replace"
    assert count["arguments"]["time_from"] == "2026-01-01T00:00:00+00:00"
    assert count["arguments"]["time_to"] == "2026-01-01T02:00:00+00:00"
    window_messages = [message for message in result["messages"] if message.name == "window"]
    assert len(window_messages) == 1
    assert "Проверяю первое появление" in window_messages[0].content
    assert not window_messages[0].tool_calls


def test_explicit_window_is_not_overwritten(patch_llm):
    agent = FakeAgent({
        "Ты — инвестигейтор": [
            ai(calls=[tc("search_events", {
                "time_from": "2025-12-31T00:00:00Z",
                "time_to": "2025-12-31T01:00:00Z",
            })]),
            ai("Проверил явный период"),
        ],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")))

    result = invoke(build_graph(), "проверь конкретный период")
    evidence = next(item for item in result["evidence"] if item["tool"] == "search_events")

    assert evidence["arguments"]["time_from"] == "2025-12-31T00:00:00Z"
    assert evidence["arguments"]["time_to"] == "2025-12-31T01:00:00Z"
