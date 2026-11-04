from datetime import datetime

from conftest import FakeAgent, FakeStructured, ai, build_graph, invoke, tc
from investigation import _normalize_intent
from llm import IntentDecision, WindowSpec


def fake_worker():
    return FakeAgent({
        "Ты — скаут": [
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("ScoutResult", {
                "note": "17 событий",
                "answer_candidate": "За сутки 17 событий",
                "evidence_ids": [],
                "escalation": {"needed": False, "reason": "", "suggested_skill": None},
            })]),
        ],
        "Ты — репортёр": [ai("За сутки 17 событий")],
    })


def count_decision():
    return IntentDecision(
        intent="complex",
        work_mode="quick",
        rationale="Нужен подсчёт за сутки",
        needs_new_data=True,
        new_investigation="new",
        window_requested=True,
        window=WindowSpec(last_minutes=1440),
    )


def test_count_last_24_hours_uses_scout_not_chat(patch_llm):
    agent = fake_worker()
    patch_llm(agent, FakeStructured(count_decision()))

    result = invoke(build_graph(), "сколько событий в догикоре за суик?")

    assert result["report_result"]["answer"] == "За сутки 17 событий"
    assert result["intent"]["inspect_memory"] is False
    assert result["evidence"][0]["tool"] == "count_events"
    start = datetime.fromisoformat(result["context"]["time_from"])
    end = datetime.fromisoformat(result["context"]["time_to"])
    assert (end - start).total_seconds() == 24 * 3600


def test_period_followup_cannot_be_replaced_by_memory(patch_llm):
    decision = IntentDecision(
        intent="continuation",
        work_mode="quick",
        rationale="Пользователь уточнил период исходного подсчёта",
        needs_new_data=False,
        inspect_memory=True,
        new_investigation="continue",
        window_requested=True,
        window=WindowSpec(last_minutes=1440),
    )
    agent = fake_worker()
    patch_llm(agent, FakeStructured(decision))
    context = {
        "id": "case-count",
        "time_from": "2026-01-01T00:00:00+00:00",
        "time_to": "2026-01-01T01:00:00+00:00",
        "base_window": {
            "time_from": "2026-01-01T00:00:00+00:00",
            "time_to": "2026-01-01T01:00:00+00:00",
        },
        "window_history": [],
    }

    result = invoke(build_graph(), "ну за последние 24 часа", context=context)

    assert result["intent"]["needs_new_data"] is True
    assert result["intent"]["inspect_memory"] is False
    assert result["intent"]["memory_query"] == ""
    assert result["report_result"]["answer"] == "За сутки 17 событий"
    assert result["evidence"][0]["tool"] == "count_events"


def test_standalone_memory_question_keeps_memory_route():
    decision = {
        "label": "simple",
        "needs_new_data": False,
        "inspect_memory": True,
        "memory_query": "VPN",
        "window_requested": True,
        "edit_existing_report": False,
        "export_data": False,
    }

    normalized = _normalize_intent(decision, {"context": {"id": "case-count"}})

    assert normalized["needs_new_data"] is False
    assert normalized["inspect_memory"] is True
    assert normalized["memory_query"] == "VPN"
