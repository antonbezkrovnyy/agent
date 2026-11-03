from conftest import FakeAgent, FakeStructured, agents_mod, ai, build_graph, intent, invoke, tc
from evidence import chain_markdown
from skills import get_skill, list_skills


def test_registry_contains_drilldown_and_pivot():
    ids = {skill.id for skill in list_skills()}

    assert {"incident_dating", "event_drilldown", "entity_pivot"} <= ids
    assert get_skill("entity_pivot").max_depth == 2
    assert get_skill("event_drilldown").max_window_updates == 0


def test_drilldown_returns_versioned_skill_run(patch_llm):
    decision = intent(mode="deep")
    decision.skill_id = "event_drilldown"
    agent = FakeAgent({
        "Ты — инвестигейтор": [ai(calls=[tc("InvestigationResult", {
            "summary": "Событие разобрано",
            "claims": [],
            "limitations": [],
            "not_checked": [],
            "skill_run": {
                "skill_id": "event_drilldown",
                "version": 1,
                "status": "done",
                "summary": "Поля события проверены",
                "window_updates": 0,
                "evidence_ids": [],
                "anchors": [],
                "visited_anchors": [],
                "depth": 0,
                "not_checked": [],
            },
        })])],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(decision))

    result = invoke(build_graph(), "разбери событие")

    assert result["active_skill"]["id"] == "event_drilldown"
    assert result["skill_runs"][0]["status"] == "done"


def pivot_state():
    return {
        "active_skill": get_skill("entity_pivot").model_dump(),
        "context": {"window_history": []},
        "evidence": [{"id": "e1"}, {"id": "e2"}],
        "anchors": [],
        "visited_anchors": [],
    }


def pivot_run(anchors, visited, depth=1):
    return {
        "summary": "Pivot",
        "limitations": [],
        "skill_run": {
            "skill_id": "entity_pivot",
            "version": 1,
            "status": "done",
            "summary": "Pivot",
            "window_updates": 0,
            "evidence_ids": ["e1"],
            "anchors": anchors,
            "visited_anchors": visited,
            "depth": depth,
            "not_checked": [],
        },
    }


def test_pivot_deduplicates_anchors_and_detects_revisit():
    state = pivot_state()
    result = {"context": {"window_history": []}, "evidence": state["evidence"]}
    first = agents_mod._skill_run_update(state, result, pivot_run(
        [{"type": "ip", "value": "1.2.3.4", "evidence_ids": ["e1"]}],
        ["IP:1.2.3.4"],
    ))
    state = {**state, **first}
    second = agents_mod._skill_run_update(state, result, pivot_run(
        [
            {"type": "ip", "value": "1.2.3.4", "evidence_ids": ["e1"]},
            {"type": "host", "value": "saturn-1", "evidence_ids": ["e2"]},
        ],
        ["ip:1.2.3.4"],
    ))

    assert len(second["anchors"]) == 2
    assert second["visited_anchors"] == ["ip:1.2.3.4"]
    assert second["skill_runs"][0]["status"] == "partial"
    assert any("повторное" in item for item in second["skill_runs"][0]["not_checked"])
    assert "Ветка не исследована в этом skill_run: host:saturn-1." in second["skill_runs"][0]["not_checked"]
    markdown = chain_markdown({**state, **second})
    assert "ip:1.2.3.4" in markdown
    assert "Обнаружено повторное посещение anchors" in markdown


def test_pivot_rejects_anchor_without_evidence_and_excess_depth():
    state = pivot_state()
    result = {"context": {"window_history": []}, "evidence": state["evidence"]}

    update = agents_mod._skill_run_update(state, result, pivot_run(
        [{"type": "ip", "value": "5.6.7.8", "evidence_ids": ["missing"]}],
        [],
        depth=3,
    ))

    assert update["anchors"] == []
    assert update["skill_runs"][0]["status"] == "partial"
    assert len(update["skill_runs"][0]["not_checked"]) >= 2
