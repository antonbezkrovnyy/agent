from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc
from contracts import InvestigationPlan, WorkItem
from llm import CriticEvidence


def plan(*items):
    return InvestigationPlan(goal="Большое расследование", items=list(items))


def item(item_id, goal=None, deps=None, required=True):
    return WorkItem(
        id=item_id,
        goal=goal or f"Проверить {item_id}",
        depends_on=deps or [],
        required=required,
    )


def result(text, replan=False):
    return ai(calls=[tc("InvestigationResult", {
        "summary": text,
        "claims": [],
        "limitations": [],
        "not_checked": [],
        "replan_needed": replan,
        "replan_reason": "Критическое Evidence отсутствует" if replan else "",
        "replan_cause": "missing_critical_evidence" if replan else "none",
    })])


def test_independent_items_execute_and_aggregate(patch_llm):
    planner = FakeStructured(plan(item("baseline"), item("duplicates")))
    agent = FakeAgent({
        "Ты — инвестигейтор": [result("Baseline готов"), result("Дубликаты проверены")],
        "Ты — репортёр": [ai("Общий отчёт")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")), planner=planner)

    result_state = invoke(build_graph(), "разбери большой всплеск")

    assert len(result_state["work_items"]) == 2
    assert {item["status"] for item in result_state["work_items"]} == {"done"}
    assert "Baseline готов" in result_state["inv_summary"]
    assert "Дубликаты проверены" in result_state["inv_summary"]
    assert result_state["inv_result"]["status"] == "done"
    assert result_state["report_result"]["answer"] == "Общий отчёт"
    from evidence import investigation_chain
    chain = investigation_chain(result_state)
    assert [item["id"] for item in chain["work_items"]] == ["baseline", "duplicates"]
    assert all(item["finished_at"] for item in chain["work_items"])


def test_dependency_failure_skips_dependent_item(patch_llm, monkeypatch):
    import critics as critics_mod

    monkeypatch.setattr(critics_mod.settings, "max_rework", 0)
    planner = FakeStructured(plan(item("baseline"), item("report-part", deps=["baseline"])))
    agent = FakeAgent({
        "Ты — инвестигейтор": [result("Нет данных")],
        "Ты — репортёр": [ai("Частичный отчёт")],
    })
    critic = FakeStructured(CriticEvidence(verdict="rework_data", feedback="Нужны данные"))
    patch_llm(agent, FakeStructured(intent(mode="deep")), critic_evidence=critic, planner=planner)

    result_state = invoke(build_graph(), "проверь зависимости")

    statuses = {item["id"]: item["status"] for item in result_state["work_items"]}
    assert statuses == {"baseline": "failed", "report-part": "skipped"}
    assert result_state["inv_result"]["status"] == "partial"
    assert "Work item report-part: skipped." in result_state["inv_result"]["limitations"]
    assert "Проверить report-part" in result_state["inv_result"]["not_checked"]
    assert agent.by_role["Ты — инвестигейтор"] == []


def test_item_critic_stops_at_rework_budget(monkeypatch):
    import critics as critics_mod

    monkeypatch.setattr(critics_mod.settings, "max_rework", 1)
    critic = FakeStructured(CriticEvidence(verdict="rework_data", feedback="Нужны данные"))
    monkeypatch.setattr(critics_mod.llm_mod, "critic_evidence_llm", critic)

    def state(attempt, rework):
        item = {
            "id": "main",
            "goal": "Проверить",
            "acceptance": "Проверить",
            "attempt": attempt,
            "result": {"summary": "Недостаточно", "claims": []},
            "status": "critic_review",
        }
        return {
            "messages": [],
            "evidence": [],
            "active_work_item": item,
            "work_items": [item],
            "rework": rework,
        }

    first = critics_mod.node_critic_evidence(state(0, 0))
    second = critics_mod.node_critic_evidence(state(1, first["rework"]))

    assert first["work_items"][0]["status"] == "pending"
    assert second["work_items"][0]["status"] == "failed"
    assert critic.calls == 2


def test_item_critic_retries_only_same_item(patch_llm):
    planner = FakeStructured(plan(item("baseline")))
    agent = FakeAgent({
        "Ты — инвестигейтор": [
            result("Неполно"),
            ai(calls=[tc("count_events", {})]),
            ai("Теперь доказано"),
        ],
        "Ты — репортёр": [ai("Готово")],
    })
    critic = FakeStructured(results=[
        CriticEvidence(verdict="rework_data", feedback="Нужна проверка"),
        CriticEvidence(verdict="ok", feedback=""),
    ])
    patch_llm(agent, FakeStructured(intent(mode="deep")), critic_evidence=critic, planner=planner)

    result_state = invoke(build_graph(), "проверь retry")

    assert result_state["work_items"][0]["attempt"] == 2
    assert result_state["work_items"][0]["status"] == "done"
    assert result_state["inv_result"]["status"] == "done"
    assert result_state["active_work_item"] == {}
    assert "Теперь доказано" in critic.systems[1]
    reporter_inputs = [
        message for system, convo in zip(agent.systems, agent.convos)
        if "Ты — репортёр" in system for message in convo
    ]
    assert not any("Продолжи baseline" in str(message.content) for message in reporter_inputs)


def test_replan_adds_only_remaining_work(patch_llm):
    planner = FakeStructured(results=[
        plan(item("initial")),
        plan(item("follow-up")),
    ])
    agent = FakeAgent({
        "Ты — инвестигейтор": [result("Нашлась новая ветка", replan=True), result("Ветка проверена")],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")), planner=planner)

    result_state = invoke(build_graph(), "проверь replan")

    assert result_state["investigation_plan"]["version"] == 2
    assert [item["id"] for item in result_state["work_items"]] == ["initial", "follow-up"]
    assert {item["status"] for item in result_state["work_items"]} == {"done"}
    assert result_state["replan_requested"] is False


def test_resume_skips_completed_items(patch_llm):
    completed = {
        "id": "old",
        "goal": "Уже проверено",
        "depends_on": [],
        "required": True,
        "requested_window": {},
        "status": "done",
        "attempt": 1,
        "result": {"status": "done", "summary": "Старый результат", "claims": []},
        "critic": {},
        "context": {},
    }
    pending = {
        "id": "remaining",
        "goal": "Осталось проверить",
        "depends_on": ["old"],
        "required": True,
        "requested_window": {},
        "status": "pending",
        "attempt": 0,
        "result": {},
        "critic": {},
        "context": {},
    }
    existing_plan = plan(item("old"), item("remaining", deps=["old"])).model_dump()
    agent = FakeAgent({
        "Ты — инвестигейтор": [result("Проверка завершена")],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")))

    import asyncio
    from config import Settings
    from investigation_graph import build_investigation_graph
    from langchain_core.messages import HumanMessage

    result_state = asyncio.run(build_investigation_graph(Settings()).ainvoke({
        "messages": [HumanMessage(content="продолжи")],
        "context": {},
        "investigation_plan": existing_plan,
        "work_items": [completed, pending],
        "active_work_item": {},
        "replan_requested": False,
        "rework": 0,
    }))

    statuses = {item["id"]: item["status"] for item in result_state["work_items"]}
    assert statuses == {"old": "done", "remaining": "done"}
    assert result_state["work_items"][0]["result"]["summary"] == "Старый результат"
    assert agent.by_role["Ты — инвестигейтор"] == []


def test_reporter_has_only_batch_evidence_reader():
    from tools import reporter_tools

    names = {tool.name for tool in reporter_tools}
    assert "get_evidence_batch" in names
    assert "get_evidence" not in names


def test_aggregate_is_partial_when_completed_item_has_partial_result():
    from planner import node_collect

    update = node_collect({
        "work_items": [{
            "id": "main",
            "goal": "Проверить",
            "required": True,
            "status": "done",
            "result": {
                "status": "partial",
                "summary": "Ограничение инструментов",
                "claims": [],
                "limitations": ["Tool limit"],
            },
        }],
        "investigation_plan": {"version": 1},
        "context": {},
    })

    assert update["inv_result"]["status"] == "partial"


def test_dependency_ids_are_normalized_together(patch_llm):
    planner = FakeStructured(plan(
        item("collect data"),
        item("report part", deps=["collect data"]),
    ))
    agent = FakeAgent({
        "Ты — инвестигейтор": [result("Данные собраны"), result("Отчёт готов")],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")), planner=planner)

    result_state = invoke(build_graph(), "проверь нормализацию зависимостей")

    statuses = {item["id"]: item["status"] for item in result_state["work_items"]}
    assert statuses == {"collect-data": "done", "report-part": "done"}
    assert result_state["work_items"][1]["depends_on"] == ["collect-data"]
    assert result_state["inv_result"]["status"] == "done"


def test_normalized_plan_preserves_reference_to_suffixed_duplicate():
    from planner import _normalized_plan

    normalized = _normalized_plan(
        plan(item("main"), item("main"), item("report", deps=["main-2"])),
        {
            "messages": [],
            "investigation_scope": {"objective": "Проверка", "max_items": 5},
            "work_items": [],
        },
    )

    assert [item.id for item in normalized.items] == ["main", "main-2", "report"]
    assert normalized.items[2].depends_on == ["main-2"]


def test_cyclic_plan_falls_back_to_single_item_with_reason(patch_llm):
    planner = FakeStructured(plan(
        item("first", deps=["second"]),
        item("second", deps=["first"]),
    ))
    agent = FakeAgent({
        "Ты — инвестигейтор": [result("Одиночная проверка завершена")],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")), planner=planner)

    result_state = invoke(build_graph(), "проверь цикл в плане")

    assert len(result_state["work_items"]) == 1
    assert result_state["work_items"][0]["id"] == "main"
    assert result_state["work_items"][0]["status"] == "done"
    assert "Цикл зависимостей: first → second → first" in result_state["investigation_plan"]["reason"]
    planner_messages = [item.content for item in result_state["messages"] if item.name == "planner"]
    assert any("План упрощён: Цикл зависимостей" in item for item in planner_messages)


def test_normalized_plan_rejects_self_dependency():
    import pytest
    from planner import DependencyCycleError, _normalized_plan

    with pytest.raises(DependencyCycleError, match="self → self"):
        _normalized_plan(
            plan(item("self", deps=["self"])),
            {
                "messages": [],
                "investigation_scope": {"objective": "Проверка", "max_items": 5},
                "work_items": [],
            },
        )


def test_planner_failure_records_fallback_reason(patch_llm):
    planner = FakeStructured(error=RuntimeError("planner unavailable"))
    agent = FakeAgent({
        "Ты — инвестигейтор": [result("Одиночная проверка завершена")],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")), planner=planner)

    result_state = invoke(build_graph(), "проверь fallback planner")

    assert len(result_state["work_items"]) == 1
    assert result_state["work_items"][0]["status"] == "done"
    assert "Причина упрощения: RuntimeError" in result_state["investigation_plan"]["reason"]
