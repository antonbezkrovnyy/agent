from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc


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
