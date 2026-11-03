from conftest import (
    FakeAgent,
    FakeStructured,
    ai,
    assert_clean,
    build_graph,
    critics,
    intent,
    invoke,
    tc,
    texts,
)
from llm import CriticReport


def test_rework_report_replaces_previous_answer(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [ai("17 событий")],
        "Ты — репортёр": [
            ai(calls=[tc("save_report", {"title": "Отчёт", "markdown": "# Кратко"})]),
            ai("Отчёт готов"),
            ai(calls=[tc("save_report", {"title": "Отчёт", "markdown": "# С выводом"})]),
            ai("Отчёт доработан"),
        ],
    })
    critic_evidence, _ = critics()
    critic_report = FakeStructured(results=[
        CriticReport(verdict="rework_report", feedback="Добавь вывод"),
        CriticReport(verdict="ok", feedback=""),
    ])
    patch_llm(agent, FakeStructured(intent()), critic_evidence, critic_report)

    result = invoke(build_graph(), "подготовь отчёт")

    assert texts(result) == ["подготовь отчёт", "Отчёт доработан"]
    assert len(result["artifacts"]) == 4
    assert result["rework"] == 1
    assert critic_report.calls == 2
    assert_clean(result)


def test_rework_data_keeps_same_window(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [
            ai(calls=[tc("search_events", {})]),
            ai("Нужны детали"),
            ai(calls=[tc("search_events", {})]),
            ai("Детали получены"),
        ],
        "Ты — репортёр": [ai("Ответ"), ai("Уточнённый ответ")],
    })
    critic_evidence, _ = critics()
    critic_report = FakeStructured(results=[
        CriticReport(verdict="rework_data", feedback="Нужны детали"),
        CriticReport(verdict="ok", feedback=""),
    ])
    patch_llm(agent, FakeStructured(intent()), critic_evidence, critic_report)

    result = invoke(build_graph(), "расследуй аномалию")
    context = result["context"]

    assert texts(result) == ["расследуй аномалию", "Уточнённый ответ"]
    assert result["rework"] == 1
    assert critic_report.calls == 2
    for evidence in result["evidence"]:
        if evidence["tool"] in ("search_events", "count_events"):
            assert evidence["arguments"]["time_from"] == context["time_from"]
            assert evidence["arguments"]["time_to"] == context["time_to"]
    assert_clean(result)
