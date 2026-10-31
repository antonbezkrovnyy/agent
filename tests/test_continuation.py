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
    assert len(second["artifacts"]) == 1
    assert texts(second) == ["подготовь отчёт", "Отчёт готов", "сократи отчёт", "Отчёт сокращён"]
    assert_clean(second)
