import json
from datetime import UTC, datetime

from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc
from contracts import InvestigationPlan, InvestigationScope, ScopeDirection, WorkItem
from prompts import structured_block, work_item_block
from scope import _normalized_scope


def test_structured_blocks_serialize_datetime_as_iso():
    moment = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)
    block = structured_block("Данные", {"window": {"time_from": moment}})
    item_block = work_item_block({
        "active_work_item": {"id": "main", "requested_window": {"time_from": moment}},
    })

    payload = json.loads(block.split(":\n", 1)[1])
    assert payload["window"]["time_from"] == "2026-11-05T03:00:00+00:00"
    assert moment.isoformat() in item_block


def scope(mode="discovery", max_items=3, optional=None, alert=None):
    return InvestigationScope(
        mode=mode,
        objective="Собрать карту проблемы",
        included=["Общая динамика", "Основные измерения", "Дополнительная ветка"],
        optional=optional or [],
        max_items=max_items,
        alert=alert or {},
    )


def plan(*ids):
    return InvestigationPlan(
        goal="Широкий запрос",
        items=[
            WorkItem(
                id=item_id,
                goal=f"Проверить {item_id}",
                acceptance=f"Получен агрегат {item_id}",
                priority="high",
                estimated_cost="low",
            )
            for item_id in ids
        ],
    )


def investigation_result(text, *, replan=False, cause="none"):
    return ai(calls=[tc("InvestigationResult", {
        "summary": text,
        "claims": [],
        "limitations": [],
        "not_checked": [],
        "replan_needed": replan,
        "replan_cause": cause,
    })])


def test_discovery_caps_work_and_exposes_optional_next_steps(patch_llm):
    optional = [ScopeDirection(objective="Разобрать конкретный host", rationale="Нужен отдельный этап")]
    planner = FakeStructured(plan("timeline", "top-values", "all-events"))
    agent = FakeAgent({
        "Ты — инвестигейтор": [investigation_result("Динамика готова"), investigation_result("Топы готовы")],
        "Ты — репортёр": [ai("Карта проблемы")],
    })
    patch_llm(
        agent,
        FakeStructured(intent(mode="deep")),
        planner=planner,
        scope=FakeStructured(scope(optional=optional)),
    )

    result = invoke(build_graph(), "продолжи и проверь всё оставшееся")

    assert result["investigation_scope"]["mode"] == "discovery"
    assert len(result["work_items"]) == 2
    assert all(item["acceptance"] for item in result["work_items"])
    assert all(item["estimated_cost"] == "low" for item in result["work_items"])
    assert result["suggestions"][0]["value"].endswith("Разобрать конкретный host")
    assert "all-events" not in {item["id"] for item in result["work_items"]}


def test_new_anomaly_without_valid_cause_does_not_replan(patch_llm):
    planner = FakeStructured(plan("main"))
    agent = FakeAgent({
        "Ты — инвестигейтор": [investigation_result("Есть ответ и новая аномалия", replan=True)],
        "Ты — репортёр": [ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent(mode="deep")), planner=planner)

    result = invoke(build_graph(), "проверь конкретную ветку")

    assert result["investigation_plan"]["version"] == 1
    assert result["replan_requested"] is False
    assert planner.calls == 1


def test_incomplete_alert_falls_back_to_bounded_discovery():
    normalized = _normalized_scope(scope(mode="alert", max_items=5))

    assert normalized.mode == "discovery"
    assert normalized.max_items == 2


def test_complete_alert_keeps_alert_scope():
    normalized = _normalized_scope(scope(
        mode="alert",
        max_items=4,
        alert={"rule_id": "R-17", "severity": "high", "entities": ["host-1"]},
    ))

    assert normalized.mode == "alert"
    assert normalized.max_items == 4
    assert normalized.alert["rule_id"] == "R-17"


def test_scope_normalizes_object_included_from_function_call():
    normalized = InvestigationScope.model_validate({
        "mode": "discovery",
        "objective": "Найти всплески",
        "included": [
            {"objective": "Построить timeline"},
            {"goal": "Сравнить с baseline"},
        ],
        "max_items": 2,
    })

    assert normalized.included == ["Построить timeline", "Сравнить с baseline"]


def test_scope_normalizes_string_optional_from_json_fallback():
    normalized = InvestigationScope.model_validate({
        "investigation_id": "ignored-extra-field",
        "mode": "discovery",
        "objective": "Найти всплески",
        "included": ["Построить timeline"],
        "optional": [
            "Разобрать самый большой пик",
            {"objective": "Проверить high importance", "rationale": "Отдельная ветка"},
        ],
        "limitations": "ignored-extra-field",
        "update_window": None,
    })

    assert [item.objective for item in normalized.optional] == [
        "Разобрать самый большой пик",
        "Проверить high importance",
    ]
    assert normalized.optional[0].rationale == ""
