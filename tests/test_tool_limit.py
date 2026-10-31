from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc, texts


def test_tool_limit_moves_workflow_forward(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("count_events", {})]),
            ai("Достаточно данных"),
        ],
        "Ты — инвестигейтор": [ai("Есть события")],
        "Ты — репортёр": [ai("Ответ готов")],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "проверь события")

    assert len(result["evidence"]) <= 4
    assert texts(result) == ["проверь события", "Ответ готов"]
    assert result["report_result"]["answer"] == "Ответ готов"
