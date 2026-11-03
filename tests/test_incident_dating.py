from conftest import FakeAgent, FakeStructured, agents_mod, ai, build_graph, intent, invoke, tc
from evidence import chain_markdown
from skills import get_skill


def dating_intent():
    decision = intent(mode="deep")
    decision.skill_id = "incident_dating"
    return decision


def test_incident_dating_definition_is_versioned():
    skill = get_skill("incident_dating")

    assert skill.version == 1
    assert skill.max_window_updates == 8
    assert "update_window" in skill.allowed_tools
    assert skill.stop_rules


def test_skill_run_is_versioned_and_added_to_chain(patch_llm):
    agent = FakeAgent({
        "Ты — инвестигейтор": [
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("InvestigationResult", {
                "summary": "Первое событие найдено",
                "claims": [],
                "limitations": [],
                "not_checked": [],
                "skill_run": {
                    "skill_id": "incident_dating",
                    "version": 1,
                    "status": "done",
                    "summary": "Первое событие локализовано",
                    "window_updates": 1,
                    "evidence_ids": [],
                    "not_checked": [],
                },
            })]),
        ],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(dating_intent()))

    result = invoke(build_graph(), "когда впервые появился сбой")

    assert result["active_skill"]["id"] == "incident_dating"
    assert result["skill_runs"][0]["version"] == 1
    assert result["skill_runs"][0]["status"] == "done"
    assert '"id": "incident_dating"' in agent.systems[0]
    assert "Skill incident_dating v1" in chain_markdown(result)


def test_missing_skill_run_is_explicit_failure(patch_llm):
    agent = FakeAgent({
        "Ты — инвестигейтор": [ai("Нет схемного результата skill")],
        "Ты — репортёр": [ai("Частичный ответ")],
    })
    patch_llm(agent, FakeStructured(dating_intent()))

    result = invoke(build_graph(), "когда впервые появился сбой")

    assert result["skill_runs"][0]["status"] == "failed"
    assert result["skill_runs"][0]["not_checked"]
    assert result["inv_result"]["limitations"]


def test_skill_window_count_comes_from_actual_history():
    state = {
        "active_skill": get_skill("incident_dating").model_dump(),
        "context": {"window_history": [{}]},
        "evidence": [],
    }
    result = {"context": {"window_history": [{}, {}, {}]}, "evidence": []}
    structured = {
        "summary": "Готово",
        "limitations": [],
        "skill_run": {
            "skill_id": "incident_dating",
            "version": 1,
            "status": "done",
            "summary": "Готово",
            "window_updates": 99,
            "evidence_ids": [],
            "not_checked": [],
        },
    }

    update = agents_mod._skill_run_update(state, result, structured)

    assert update["skill_runs"][0]["window_updates"] == 2
