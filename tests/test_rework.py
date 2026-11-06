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

    assert texts(result)[0] == "подготовь отчёт"
    assert texts(result)[1].startswith("Отчёт доработан")
    assert len(result["artifacts"]) == 2
    assert result["rework"] == 1
    assert result["report_result"]["verification"]["status"] == "consistent_with_evidence"
    assert result["last_report"]["verification"]["status"] == "consistent_with_evidence"
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

    assert texts(result)[0] == "расследуй аномалию"
    assert texts(result)[1].startswith("Уточнённый ответ")
    assert result["rework"] == 1
    assert critic_report.calls == 2
    for evidence in result["evidence"]:
        if evidence["tool"] in ("search_events", "count_events"):
            assert evidence["arguments"]["time_from"] == context["time_from"]
            assert evidence["arguments"]["time_to"] == context["time_to"]
    assert_clean(result)


def test_rework_budget_resets_on_new_user_message(patch_llm):
    graph = build_graph()
    agent = FakeAgent({
        "Ты — скаут": [ai("Данные")],
        "Ты — инвестигейтор": [ai("Данные")],
        "Ты — репортёр": [
            ai(calls=[tc("save_report", {"title": "Отчёт", "markdown": "# Первый"})]),
            ai("Первый ответ"),
            ai(calls=[tc("save_report", {"title": "Отчёт", "markdown": "# Первый исправленный"})]),
            ai("Первый исправленный"),
        ],
    })
    critic_evidence, _ = critics()
    first_critic = FakeStructured(results=[
        CriticReport(verdict="rework_report", feedback="Правка"),
        CriticReport(verdict="ok", feedback=""),
    ])
    patch_llm(agent, FakeStructured(intent()), critic_evidence, first_critic)
    invoke(graph, "подготовь отчёт", thread_id="rework-reset")

    agent2 = FakeAgent({
        "Ты — репортёр": [
            ai(calls=[tc("save_report", {"title": "Отчёт", "markdown": "# Второй"})]),
            ai("Второй ответ"),
            ai(calls=[tc("save_report", {"title": "Отчёт", "markdown": "# Второй исправленный"})]),
            ai("Второй исправленный"),
        ],
    })
    second_critic = FakeStructured(results=[
        CriticReport(verdict="rework_report", feedback="Ещё правка"),
        CriticReport(verdict="ok", feedback=""),
    ])
    patch_llm(
        agent2,
        FakeStructured(intent("continuation", new=False, edit=True)),
        critic_report=second_critic,
    )
    second = invoke(graph, "сохрани новую версию", thread_id="rework-reset")

    assert second_critic.calls == 2
    assert second["report_result"]["answer"].startswith("Второй исправленный")
    assert second["rework"] == 1
    assert second["active_work_item"] == {}


def test_exhausted_rework_is_reported_as_unresolved(patch_llm, monkeypatch):
    import critics as critics_mod
    import graph as graph_mod

    monkeypatch.setattr(critics_mod.settings, "max_rework", 0)
    monkeypatch.setattr(graph_mod.settings, "max_rework", 0)
    agent = FakeAgent({
        "Ты — скаут": [ai("Данные")],
        "Ты — инвестигейтор": [ai("Данные")],
        "Ты — репортёр": [ai("Ответ с дефектом")],
    })
    critic_evidence, _ = critics()
    critic_report = FakeStructured(CriticReport(verdict="rework_report", feedback="Нужна правка"))
    patch_llm(agent, FakeStructured(intent()), critic_evidence, critic_report)

    result = invoke(build_graph(), "подготовь отчёт")

    assert result["progress"]["text"] == "Готово с неустранёнными замечаниями критика"
    assert "Нужна правка" in result["report_result"]["limitations"][-1]
    assert result["report_result"]["verification"]["status"] == "issues_open"


def test_rework_without_file_update_is_explicit_failure(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("Данные")],
        "Ты — инвестигейтор": [ai("Данные")],
        "Ты — репортёр": [
            ai(calls=[tc("save_report", {"title": "Отчёт", "markdown": "# Ошибочная версия"})]),
            ai("Первая версия"),
            ai("Файл якобы исправлен, но сохранять его не буду"),
        ],
    })
    critic_evidence, _ = critics()
    critic_report = FakeStructured(results=[
        CriticReport(
            verdict="rework_report",
            feedback="Исправь ошибку",
            issues=[{"claim": "Ошибочное утверждение", "reason": "Не подтверждено", "suggested_action": "rework_report"}],
        ),
        CriticReport(verdict="ok", feedback=""),
    ])
    patch_llm(agent, FakeStructured(intent()), critic_evidence, critic_report)

    result = invoke(build_graph(), "подготовь отчёт")

    assert critic_report.calls == 1
    assert result["critic"]["verdict"] == "update_failed"
    assert result["progress"]["text"] == "Не удалось исправить отчёт"
    assert result["report_result"]["verification"]["status"] == "update_failed"
    assert result["last_report"]["verification"]["status"] == "update_failed"
    assert result["last_report"]["revision"] == 1
    assert "Ошибочное утверждение" in result["last_report"]["markdown"]
    assert "Фактические данные" in result["last_report"]["markdown"]
    assert result["messages"][-1].content.startswith("Reporter не сохранил изменённый Markdown")
    assert len(result["artifacts"]) == 2
    assert_clean(result)
