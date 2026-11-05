from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc


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

    assert len(result["evidence"]) == 10
    scout_metrics = next(item for item in result["run_metrics"] if item["agent"] == "scout")
    assert scout_metrics["segments"] == 1
    public_answers = [
        message.content for message in result["messages"]
        if message.name in (None, "chat", "reporter")
    ]
    assert public_answers == ["проверь события", "Ответ готов"]
    assert result["report_result"]["answer"] == "Ответ готов"


def test_rework_and_new_message_reset_limit(patch_llm, monkeypatch):
    from conftest import CriticEvidence
    import agents as agents_mod

    async def fallback(agent, result, schema):
        return agents_mod._fallback_limited(schema, agent).model_dump()

    monkeypatch.setattr(agents_mod, "_finalize_limited", fallback)
    monkeypatch.setattr(agents_mod.settings, "agent_max_continuations", 0)

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


def test_limit_finalizer_returns_schema_without_raw_middleware(monkeypatch):
    import asyncio
    import agents as agents_mod
    from llm import ReporterResult

    class Structured:
        def with_structured_output(self, schema, method):
            return self

        def with_fallbacks(self, fallbacks):
            return self

        async def ainvoke(self, messages, config=None):
            return ReporterResult(answer="Итог по собранным Evidence", limitations=[])

    monkeypatch.setattr(agents_mod.llm_mod, "model_for_role", lambda role: Structured())
    result = {"messages": [], "context": {"id": "case-limit"}, "evidence": []}

    structured = asyncio.run(agents_mod._finalize_limited(
        "reporter",
        result,
        agents_mod.llm_mod.ReporterResult,
    ))

    assert structured["answer"] == "Итог по собранным Evidence"
    assert "Tool call limit reached" not in structured["answer"]
    assert "Исчерпан лимит 60 вызовов" in structured["limitations"][0]


def test_get_evidence_batch_reads_records_in_one_call():
    import json
    from types import SimpleNamespace
    from tools import get_evidence_batch

    runtime = SimpleNamespace(state={"evidence": [
        {"id": "ev-1", "result": {"count": 1}},
        {"id": "ev-2", "result": {"count": 2}},
    ]})

    payload = json.loads(get_evidence_batch.func(["ev-2", "missing"], runtime=runtime))

    assert [item["id"] for item in payload["items"]] == ["ev-2"]
    assert payload["missing_ids"] == ["missing"]


def test_reporter_automatically_continues_after_limit(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai("Данные проверены")],
        "Ты — инвестигейтор": [ai("Данные проверены")],
        "Ты — репортёр": [
            *[ai(calls=[tc("list_findings", {})]) for _ in range(61)],
            ai("Отчёт после автоматического продолжения"),
        ],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "продолжи и заверши отчёт")

    assert result["report_result"]["answer"] == "Отчёт после автоматического продолжения"
    assert not result["report_result"]["limitations"]
    assert result["tool_usage"]["reporter"] == 62
