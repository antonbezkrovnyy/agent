from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc, texts


def test_tool_limit_moves_workflow_forward(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [
            *[ai(calls=[tc("count_events", {})]) for _ in range(11)],
            ai("Достаточно данных"),
        ],
        "Ты — инвестигейтор": [ai("Есть события")],
        "Ты — репортёр": [ai("Ответ готов")],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "проверь события")

    assert len(result["evidence"]) <= 10
    assert texts(result) == ["проверь события", "Ответ готов"]
    assert result["report_result"]["answer"] == "Ответ готов"


def test_rework_and_new_message_reset_limit(patch_llm):
    from conftest import CriticEvidence

    agent = FakeAgent({
        "Ты — скаут": [ai("Готово")],
        "Ты — инвестигейтор": [
            *[ai(calls=[tc("count_events", {})]) for _ in range(31)],
            *[ai(calls=[tc("count_events", {})]) for _ in range(31)],
        ],
        "Ты — репортёр": [ai("Ответ с ограничением")],
    })
    critic = FakeStructured(results=[
        CriticEvidence(verdict="rework_data", feedback="Нужно больше данных"),
        CriticEvidence(verdict="ok", feedback=""),
    ])
    patch_llm(agent, FakeStructured(intent()), critic_evidence=critic)
    graph = build_graph()

    first = invoke(graph, "первый запрос", thread_id="turn-limit")

    assert first["rework"] == 1
    assert first["tool_usage"]["investigator"] == 31
    assert len(first["evidence"]) == 60

    agent2 = FakeAgent({
        "Ты — инвестигейтор": [
            *[ai(calls=[tc("count_events", {})]) for _ in range(31)],
        ],
        "Ты — репортёр": [ai("Новый ответ")],
    })
    patch_llm(agent2, FakeStructured(intent("continuation", new=True, mode="deep")))
    second = invoke(graph, "второй запрос", thread_id="turn-limit")

    assert second["tool_usage"]["investigator"] == 31
    assert len(second["evidence"]) - len(first["evidence"]) == 30
