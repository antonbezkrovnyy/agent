"""Классификация интента, контекст и смена текущего расследования."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langgraph.types import Overwrite

import llm as llm_mod
from agents import _agent_convo_messages
from observability import trace_config
from progress import agent_progress, simple_progress
from prompts import compose, intent_hint_block, system_prompt
from artifact_store import artifact_from_data_url, artifact_url
from reporting import build_report
from skills import get_skill
from state import State


def _intent_messages(state: State) -> list[BaseMessage]:
    system = system_prompt(compose("base", "intent"), intent_hint_block(state))
    return [SystemMessage(content=system), *_agent_convo_messages(state)]


def _intent_structured(state: State) -> dict | None:
    """Structured output с настроенным fallback на JSON mode."""
    try:
        decision = llm_mod.intent_llm.invoke(
            _intent_messages(state),
            config=trace_config("intent", state),
        )
    except Exception:
        return None
    if decision is None or not getattr(decision, "intent", None):
        return None
    window = getattr(decision, "window", None)
    return {
        "label": decision.intent,
        "work_mode": getattr(decision, "work_mode", "quick"),
        "rationale": getattr(decision, "rationale", ""),
        "needs_new_data": getattr(decision, "needs_new_data", False),
        "edit_existing_report": getattr(decision, "edit_existing_report", False),
        "export_data": getattr(decision, "export_data", False),
        "inspect_memory": getattr(decision, "inspect_memory", False),
        "memory_query": getattr(decision, "memory_query", ""),
        "new_investigation": getattr(decision, "new_investigation", "continue"),
        "window_requested": getattr(decision, "window_requested", False),
        "skill_id": getattr(decision, "skill_id", None),
        "related_investigation_id": getattr(decision, "related_investigation_id", None),
        "confidence": getattr(decision, "confidence", 0.0),
        "escalation_reason": getattr(decision, "escalation_reason", ""),
        "window": window.model_dump() if window else {"last_minutes": 60},
    }


def _normalize_intent(decision: dict, state: State) -> dict:
    """Разрешить противоречивые флаги structured output без keyword-классификации."""
    decision = dict(decision)
    if (
        state.get("context")
        and decision.get("label") in ("complex", "continuation")
        and decision.get("window_requested")
    ):
        decision["needs_new_data"] = True
    if decision.get("needs_new_data") or decision.get("export_data"):
        decision["inspect_memory"] = False
        decision["memory_query"] = ""
    if decision.get("inspect_memory"):
        decision["needs_new_data"] = False
        decision["edit_existing_report"] = False
        decision["export_data"] = False
    return decision


def _intent_fallback(state: State) -> dict:
    """Консервативный fallback без семантических правил по словам."""
    has_context = bool(state.get("context"))
    return {
        "label": "continuation" if has_context else "complex",
        "work_mode": "deep",
        "rationale": "LLM-классификатор недоступен; выполняется безопасная проверка данных без выбора skill",
        "needs_new_data": True,
        "edit_existing_report": False,
        "export_data": False,
        "inspect_memory": False,
        "memory_query": "",
        "new_investigation": "continue" if has_context else "new",
        "window_requested": False,
        "skill_id": None,
        "related_investigation_id": None,
        "confidence": 0.0,
        "escalation_reason": "LLM-классификатор недоступен",
        "window": {"last_minutes": 60},
    }


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment.astimezone(UTC)


def _new_context(decision: dict) -> dict:
    spec = decision.get("window") or {}
    end = _parse_time(spec.get("time_to")) or datetime.now(UTC)
    start = _parse_time(spec.get("time_from")) or end - timedelta(
        minutes=spec.get("last_minutes") or 60
    )
    window = {"time_from": start.isoformat(), "time_to": end.isoformat()}
    return {
        "id": uuid4().hex,
        **window,
        "timezone": "UTC",
        "base_window": dict(window),
        "focus_window": None,
        "window_history": [],
    }


def _focus_context(context: dict, decision: dict) -> dict:
    requested = _new_context(decision)
    active = {"time_from": requested["time_from"], "time_to": requested["time_to"]}
    previous = {"time_from": context["time_from"], "time_to": context["time_to"]}
    history = list(context.get("window_history") or [])
    history.append({
        "action": "focus",
        "from": previous,
        "to": active,
        "reason": "Пользователь указал новый период",
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    })
    return {
        **context,
        **active,
        "focus_window": dict(active),
        "window_history": history,
    }


def _work_progress(state: State, decision: dict) -> dict:
    if decision.get("inspect_memory"):
        return simple_progress("memory_inspect", "Читаю содержимое памяти")
    if decision.get("export_data"):
        return simple_progress("export", "Готовлю полную выгрузку")
    if decision.get("needs_new_data"):
        if decision.get("work_mode") == "deep":
            text = {
                "incident_dating": "Ищу первое появление",
                "event_drilldown": "Разбираю событие",
                "entity_pivot": "Проверяю связанные сущности",
            }.get(decision.get("skill_id"), "Веду глубокий анализ")
            return agent_progress(state, "investigator", text, "investigator")
        return agent_progress(state, "scout", "Быстро проверяю журнал", "scout")
    if decision.get("label") == "simple":
        return simple_progress("chat", "Формирую ответ")
    return agent_progress(state, "reporter", "Формирую ответ", "reporter")


def node_intent(state: State) -> dict:
    """Выбрать quick/deep и автоматически обновить контекст расследования."""
    decision = _normalize_intent(_intent_structured(state) or _intent_fallback(state), state)
    transition = decision.get("new_investigation") or "continue"
    if transition == "uncertain":
        transition = "continue"
    decision["new_investigation"] = transition
    mode = decision.get("work_mode") or "quick"
    previous_mode = state.get("work_mode")
    history = [] if transition == "new" else list(state.get("depth_history") or [])
    if mode != previous_mode or not history:
        history.append({
            "from": previous_mode,
            "to": mode,
            "reason": "user_request",
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        })
    update = {
        "intent": decision,
        "work_mode": mode,
        "depth_history": history,
        "transition": transition,
    }
    if decision.get("needs_new_data") or transition == "new" or mode == "quick":
        skill = get_skill(decision.get("skill_id")) if mode == "deep" else None
        update["active_skill"] = {
            **skill.model_dump(),
            "selected_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        } if skill else {}
    if decision.get("needs_new_data") or decision.get("export_data"):
        if not state.get("context") and not decision.get("related_investigation_id"):
            update["context"] = _new_context(decision)
        elif transition != "new" and decision.get("window_requested") and state.get("context"):
            update["context"] = _focus_context(state["context"], decision)
    has_history = bool(state.get("evidence") or state.get("findings") or state.get("last_report"))
    if has_history and transition == "new":
        update["progress"] = simple_progress("archive", "Сохраняю прежний отчёт")
    else:
        update["progress"] = _work_progress({**state, **update}, decision)
    return update


def _archive_update(state: State) -> dict:
    if state.get("archive_done"):
        return {}
    report = state.get("last_report") or {}
    result = state.get("report_result") or {}
    title = report.get("title") or "Предыдущее расследование"
    markdown = report.get("markdown") or result.get("answer") or state.get("inv_summary") or ""
    if not markdown:
        return {"archive_done": 1}
    _, url, _ = build_report(title, markdown)
    artifact = artifact_from_data_url(url, state.get("context") or {})
    message = AIMessage(content="Прежний отчёт сохранён файлом.", name="archive")
    message.artifacts = [artifact_url(artifact)]
    return {"archive_done": 1, "artifacts": [artifact], "messages": [message]}


def node_archive(state: State) -> dict:
    update = _archive_update(state)
    if state.get("transition") == "new":
        update.update({
            "context": _new_context(state["intent"]),
            "evidence": Overwrite([]),
            "findings": Overwrite([]),
            "hypotheses": Overwrite([]),
            "skill_runs": Overwrite([]),
            "anchors": [],
            "visited_anchors": [],
            "last_report": {},
            "inv_summary": "",
            "memory_operations": Overwrite([]),
            "investigation_scope": {},
            "investigation_plan": {},
            "work_items": Overwrite([]),
            "active_work_item": {},
            "replan_requested": False,
        })
        update["progress"] = _work_progress({**state, **update}, state["intent"])
    return update
