from conftest import (
    FakeAgent,
    FakeStructured,
    ai,
    assert_clean,
    build_graph,
    intent,
    invoke,
    tc,
    texts,
)


def test_report_edit_keeps_evidence_and_context(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [
            ai(calls=[tc("log_finding", {
                "claim": "17 событий", "evidence_ids": [], "next_action": "отчёт",
            })]),
            ai("17 событий"),
        ],
        "Ты — репортёр": [
            ai(calls=[tc("save_report", {"title": "Отчёт", "markdown": "# 17 событий"})]),
            ai("Отчёт готов"),
        ],
    })
    patch_llm(agent, FakeStructured(intent()))
    graph = build_graph()
    first = invoke(graph, "подготовь отчёт", thread_id="continuation")

    agent2 = FakeAgent({
        "Ты — репортёр": [
            ai(calls=[tc("save_report", {"title": "Коротко", "markdown": "# 17"})]),
            ai("Отчёт сокращён"),
        ],
    })
    patch_llm(agent2, FakeStructured(intent("continuation", new=False, edit=True)))
    second = invoke(graph, "сократи отчёт", thread_id="continuation")

    assert second["context"]["id"] == first["context"]["id"]
    assert second["evidence"] == first["evidence"]
    assert second["findings"] == first["findings"]
    assert len(second["artifacts"]) == 2
    assert second["investigation_scope"] == first["investigation_scope"]
    assert second["investigation_plan"] == first["investigation_plan"]
    assert second["work_items"] == first["work_items"]
    assert len(second["run_metrics"]) == len(first["run_metrics"]) + 1
    assert "Тип: `targeted`" in second["last_report"]["markdown"]
    assert "## План расследования" in second["last_report"]["markdown"]
    assert "Продолжи main" not in second["last_report"]["markdown"]
    assert "сократи отчёт" in second["last_report"]["markdown"]
    actual_texts = texts(second)
    assert actual_texts[0] == "подготовь отчёт"
    assert actual_texts[1].startswith("Отчёт готов")
    assert actual_texts[2] == "сократи отчёт"
    assert actual_texts[3].startswith("Отчёт сокращён")
    assert second["report_result"]["verification"]["status"] == "consistent_with_evidence"
    assert_clean(second)


def test_report_request_without_existing_file_goes_to_reporter(patch_llm):
    agent = FakeAgent({
        "Ты — репортёр": [
            ai(calls=[tc("save_report", {
                "title": "Отчёт расследования",
                "markdown": "# Выводы и цепочка",
            })]),
            ai("Отчёт готов"),
        ],
    })
    decision = intent("continuation", new=False, edit=True)
    decision.export_data = False
    patch_llm(agent, FakeStructured(decision))

    result = invoke(build_graph(), "пришли мне отчет о расследовании")

    assert result["report_result"]["answer"].startswith("Отчёт готов")
    assert result["last_report"]["title"] == "Отчёт расследования"
    assert len(result["artifacts"]) == 1
    assert all(item["tool"] != "extract_data" for item in result["evidence"])
