import asyncio

from conftest import (
    FakeAgent,
    agents_mod,
    FakeStructured,
    ai,
    build_graph,
    intent,
    invoke,
    tc,
)


def start(graph, text, thread_id):
    return asyncio.run(graph.ainvoke(
        {"messages": [{"role": "human", "content": text}]},
        {"configurable": {"thread_id": thread_id}},
    ))


def test_investigation_runs_without_budget_interrupt(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [ai("17 событий")],
        "Ты — репортёр": [ai("Ответ")],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = start(build_graph(), "проверь события", "no-budget")

    assert "__interrupt__" not in result
    assert result["context"]["time_from"]
    assert result["progress"]["state"] == "done"
    assert result["report_result"]["answer"] == "Ответ"


def investigation_agent():
    return FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [
            ai(calls=[tc("log_finding", {
                "claim": "17 событий", "evidence_ids": [], "next_action": "отчёт",
            })]),
            ai("17 событий"),
        ],
        "Ты — репортёр": [
            ai(calls=[tc("save_report", {"title": "Отчёт", "markdown": "# 17"})]),
            ai("Отчёт готов"),
        ],
    })


def test_new_investigation_archives_without_confirmation(patch_llm):
    patch_llm(investigation_agent(), FakeStructured(intent()))
    graph = build_graph()
    first = invoke(graph, "подготовь отчёт", thread_id="new-investigation")

    agent2 = FakeAgent({
        "Ты — скаут": [ai("Новый скаут")],
        "Ты — инвестигейтор": [ai("Новые данные")],
        "Ты — репортёр": [ai("Новый ответ")],
    })
    patch_llm(agent2, FakeStructured(intent(transition="new")))
    result = start(graph, "новая задача", "new-investigation")

    assert "__interrupt__" not in result
    assert any("Прежний отчёт сохранён" in m.content for m in result["messages"])
    assert result["context"]["id"] != first["context"]["id"]
    assert result["evidence"] == []
    assert result["findings"] == []
    assert result["inv_summary"] == "Новые данные"
    assert "Резюме инвестигейтора:\n17 событий" not in agent2.systems[-1]
    assert result["report_result"]["answer"] == "Новый ответ"


def test_uncertain_investigation_continues_without_archive(patch_llm):
    patch_llm(investigation_agent(), FakeStructured(intent()))
    graph = build_graph()
    first = invoke(graph, "подготовь отчёт", thread_id="uncertain-continuation")

    agent2 = FakeAgent({
        "Ты — инвестигейтор": [ai("Дополнительные данные")],
        "Ты — репортёр": [ai("Тот же контекст")],
    })
    patch_llm(agent2, FakeStructured(intent("continuation", new=True, transition="uncertain", mode="deep")))
    result = start(graph, "возможно другая задача", "uncertain-continuation")

    assert "__interrupt__" not in result
    assert result["context"]["id"] == first["context"]["id"]
    assert result["evidence"] == first["evidence"]
    assert not any("Прежний отчёт сохранён" in m.content for m in result["messages"])


def test_continuation_reuses_context_without_interrupt(patch_llm):
    patch_llm(investigation_agent(), FakeStructured(intent()))
    graph = build_graph()
    first = invoke(graph, "подготовь отчёт", thread_id="continue-investigation")

    agent2 = FakeAgent({
        "Ты — инвестигейтор": [ai("Дополнительные данные")],
        "Ты — репортёр": [ai("Уточнённый ответ")],
    })
    patch_llm(agent2, FakeStructured(intent("continuation", new=True, transition="continue", mode="deep")))
    result = invoke(graph, "продолжи анализ", thread_id="continue-investigation")

    assert result["context"]["id"] == first["context"]["id"]
    assert result["evidence"] == first["evidence"]
    assert result["findings"] == first["findings"]
    assert result["scout_msgs"] == []
    assert result["report_result"]["answer"] == "Уточнённый ответ"


def test_agent_timeout_returns_limitation(patch_llm, monkeypatch):
    class SlowAgent:
        async def ainvoke(self, agent_input, config=None):
            await asyncio.sleep(1)

    patch_llm(FakeAgent({}), FakeStructured(intent()))
    monkeypatch.setattr(agents_mod.settings, "agent_timeout_seconds", 0.001)
    agents_mod._agents["investigator"] = SlowAgent()
    state = {
        "messages": [],
        "callback_url": "",
        "artifacts": [],
        "findings": [],
        "evidence": [],
        "context": {},
        "last_report": {},
        "inv_summary": "",
        "inv_result": {},
        "scout_note": "",
        "scout_result": {},
        "critic": {},
        "rework": 0,
    }

    result, _, _ = asyncio.run(agents_mod._run_agent("investigator", state, "inv_msgs"))

    assert result["structured_response"].limitations == [
        "Технический таймаут: этап остановлен из-за слишком долгого ответа."
    ]
