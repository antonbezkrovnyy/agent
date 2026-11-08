"""Планирование и выполнение Deep Investigation по work items."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import llm as llm_mod
from execution_audit import audit_messages
from control_overrides import with_prompt_override
from config import Settings
from contracts import InvestigationPlan, InvestigationScope, WorkItem
from observability import trace_config
from progress import agent_progress, simple_progress
from prompts import compose, context_block, structured_block, system_prompt
from state import State, merge_work_items

settings = Settings()


def _current_task(state: State) -> str:
    messages = [
        getattr(item, "text", item.content) for item in state.get("messages") or []
        if getattr(item, "type", None) == "human" and getattr(item, "text", item.content)
    ]
    return messages[-1] if messages else "Текущее расследование"


def _window(state: State) -> dict:
    context = state.get("context") or {}
    return {
        "time_from": context.get("time_from"),
        "time_to": context.get("time_to"),
    }


def _fallback_plan(state: State) -> InvestigationPlan:
    scope = state.get("investigation_scope") or {}
    objective = scope.get("objective") or _current_task(state)
    included = scope.get("included") or [objective]
    return InvestigationPlan(
        goal=objective,
        reason="Безопасный одиночный work item без декомпозиции",
        items=[WorkItem(
            id="main",
            goal=included[0],
            acceptance=included[0],
            priority="high",
            estimated_cost="low" if scope.get("mode") == "discovery" else "medium",
            requested_window=_window(state),
        )],
    )


def _normalized_plan(plan: InvestigationPlan, state: State) -> InvestigationPlan:
    previous = state.get("investigation_plan") or {}
    scope = InvestigationScope.model_validate(
        state.get("investigation_scope") or {"objective": _current_task(state)}
    )
    plan = plan.model_copy(deep=True)
    plan.items = plan.items[:min(settings.planner_max_items, scope.max_items)]
    plan.goal = scope.objective
    plan.version = int(previous.get("version") or 0) + 1
    existing_ids = {item.get("id") for item in state.get("work_items") or []}
    seen = set()
    id_map = {}
    for index, item in enumerate(plan.items, start=1):
        original_id = item.id
        base = original_id.strip().replace(" ", "-") or f"item-{index}"
        item.id = base
        suffix = 2
        while item.id in seen:
            item.id = f"{base}-{suffix}"
            suffix += 1
        seen.add(item.id)
        id_map.setdefault(original_id, item.id)
        if original_id.strip():
            id_map.setdefault(original_id.strip(), item.id)
    available_ids = seen.union(existing_ids)
    required_count = 0
    for item in plan.items:
        item.depends_on = list(dict.fromkeys(
            id_map.get(dep, dep) for dep in item.depends_on
        ))
        item.depends_on = [dep for dep in item.depends_on if dep in available_ids]
        if not item.requested_window:
            item.requested_window = _window(state)
        item.acceptance = item.acceptance.strip() or item.goal
        if item.required:
            required_count += 1
            if required_count > settings.scope_required_max_items:
                item.required = False
        item.status = "pending"
        item.attempt = 0
        item.result = {}
        item.metrics = {}
        item.critic = {}
        item.context = {}
    return plan


def node_planner(state: State) -> dict:
    memory_control = (state.get("intent") or {}).get("memory_control_required")
    if state.get("investigation_plan") and not state.get("replan_requested") and not memory_control:
        return {}
    if not settings.investigation_planner_enabled:
        plan = _fallback_plan(state)
    else:
        payload = {
            "task": _current_task(state),
            "intent": state.get("intent") or {},
            "active_skill": state.get("active_skill") or {},
            "investigation_scope": state.get("investigation_scope") or {},
            "scout_result": state.get("scout_result") or {},
            "current_context": state.get("context") or {},
            "current_result": state.get("inv_result") or {},
            "existing_work_items": state.get("work_items") or [],
            "replan": bool(state.get("replan_requested")),
        }
        messages = [
            SystemMessage(content=system_prompt(
                compose("base", "planner"),
                context_block(state),
                structured_block("Данные для планирования", payload),
            )),
            HumanMessage(content="Сформируй InvestigationPlan по текущей задаче."),
        ]
        messages = with_prompt_override(state, "planner", messages)
        audit_messages("planner", state, messages)
        try:
            from deadline import run_expired

            if run_expired(state):
                raise TimeoutError("Investigation run deadline exceeded")
            response = llm_mod.planner_llm.invoke(messages, config=trace_config("planner", state))
            plan = response if isinstance(response, InvestigationPlan) else InvestigationPlan.model_validate(response)
        except Exception:
            plan = _fallback_plan(state)
    plan = _normalized_plan(plan, state)
    goal = plan.goal or (state.get("investigation_scope") or {}).get("objective") or "Текущее расследование"
    notification_lines = [f"План расследования (версия {plan.version}): {goal}"]
    notification_lines.extend(f"{index}. {item.goal}" for index, item in enumerate(plan.items, start=1))
    notification = AIMessage(content="\n".join(notification_lines), name="planner")
    return {
        "investigation_plan": plan.model_dump(),
        "work_items": plan.model_dump()["items"],
        "active_work_item": {},
        "replan_requested": False,
        "progress": simple_progress(
            "planner",
            f"План: {len(plan.items)} {'часть' if len(plan.items) == 1 else 'частей'} расследования",
        ),
        "messages": [notification],
    }


def _item_context(state: State, item: dict) -> dict:
    context = state.get("context") or {}
    requested = item.get("requested_window") or {}
    active = {
        "time_from": requested.get("time_from") or context.get("time_from"),
        "time_to": requested.get("time_to") or context.get("time_to"),
    }
    return {
        **context,
        **active,
        "focus_window": active if requested else context.get("focus_window"),
    }


def ready_items(state: State) -> list[dict]:
    items = state.get("work_items") or []
    done = {item.get("id") for item in items if item.get("status") == "done"}
    return [
        item for item in items
        if item.get("status") == "pending"
        and all(dep in done for dep in item.get("depends_on") or [])
    ]


def item_input(state: State, item: dict) -> dict:
    item = {**item, "status": "running"}
    return {
        **state,
        "active_work_item": item,
        "context": _item_context(state, item),
        "critic": item.get("critic") or {},
        "rework": item.get("attempt") or 0,
        "inv_result": {},
        "inv_summary": "",
    }


def _blocked_items(state: State) -> list[dict]:
    if ready_items(state):
        return []
    blocked = []
    for item in state.get("work_items") or []:
        if item.get("status") == "pending":
            blocked.append({
                "id": item["id"],
                "status": "skipped",
                "result": {
                    "status": "failed",
                    "summary": "Пропущено из-за незавершённой зависимости.",
                    "claims": [],
                    "limitations": ["Зависимость не завершена."],
                    "not_checked": [item.get("goal") or item["id"]],
                },
            })
    return blocked


def _aggregate(state: State) -> dict:
    items = state.get("work_items") or []
    claims = []
    limitations = []
    not_checked = []
    summaries = []
    recommended = None
    for item in items:
        result = item.get("result") or {}
        if result.get("summary"):
            summaries.append(
                result["summary"] if len(items) == 1 else f"{item.get('goal')}: {result['summary']}"
            )
        claims.extend(result.get("claims") or [])
        limitations.extend(result.get("limitations") or [])
        not_checked.extend(result.get("not_checked") or [])
        if item.get("status") in ("failed", "skipped"):
            note = f"Work item {item.get('id')}: {item.get('status')}."
            limitations.append(note)
            not_checked.append(item.get("goal") or item["id"])
        recommended = result.get("recommended_export_query") or recommended
    required_done = all(
        item.get("status") == "done" and (item.get("result") or {}).get("status") == "done"
        for item in items if item.get("required")
    )
    degraded = any(
        item.get("status") in ("failed", "skipped")
        or (item.get("result") or {}).get("status") in ("partial", "failed")
        for item in items
    )
    status = "done" if items and required_done and not degraded else "partial"
    result = {
        "status": status,
        "summary": "\n".join(summaries) or "Нет завершённых work items.",
        "claims": claims,
        "limitations": list(dict.fromkeys(limitations)),
        "not_checked": list(dict.fromkeys(not_checked)),
        "recommended_export_query": recommended,
        "skill_run": None,
    }
    context = state.get("context") or {}
    history = []
    for item in items:
        for event in (item.get("context") or {}).get("window_history") or []:
            if event not in history:
                history.append(event)
    result_context = (
        items[0].get("context") if len(items) == 1 and items[0].get("context")
        else {**context, "window_history": history}
    )
    return {
        "inv_result": result,
        "inv_summary": result["summary"],
        "context": result_context,
    }


def node_collect(state: State) -> dict:
    items = state.get("work_items") or []
    done = sum(item.get("status") == "done" for item in items)
    scope = state.get("investigation_scope") or {}
    update = {
        "active_work_item": {},
        "progress": {
            **simple_progress("planner", f"Готово {done}/{len(items)} проверок"),
            "scope_mode": scope.get("mode") or "",
            "objective": scope.get("objective") or "",
            "completed_items": done,
            "total_items": len(items),
        },
    }
    blocked = _blocked_items(state)
    aggregate_state = state
    if blocked:
        update["work_items"] = blocked
        aggregate_state = {
            **state,
            "work_items": merge_work_items(items, blocked),
        }
    replan = any(
        (item.get("result") or {}).get("replan_needed")
        and (item.get("result") or {}).get("replan_cause") in {
            "acceptance_failed", "conclusion_invalid", "missing_critical_evidence",
        }
        for item in items if item.get("status") == "done"
    )
    current_version = (state.get("investigation_plan") or {}).get("version") or 1
    if replan and current_version < settings.planner_max_revisions:
        update["replan_requested"] = True
        update["progress"] = simple_progress("planner", "Уточняю план по новым данным")
    elif ready_items(state):
        return update
    else:
        update.update(_aggregate(aggregate_state))
        update["progress"] = agent_progress(state, "reporter", "Формирую ответ", "reporter")
    return update
