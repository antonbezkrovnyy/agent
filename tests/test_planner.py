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
    assert agent.by_role["Ты — инвестигейтор"] == []


def test_item_critic_retries_only_same_item(patch_llm):
    planner = FakeStructured(plan(item("baseline")))
    agent = FakeAgent({
        "Ты — инвестигейтор": [result("Неполно"), result("Теперь доказано")],
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
