"""Structured InvestigationScope: границы текущего этапа до планирования."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import llm as llm_mod
from execution_audit import audit_messages
from control_overrides import with_prompt_override
from config import Settings
from contracts import InvestigationScope
from observability import trace_config
from progress import simple_progress
from prompts import compose, context_block, structured_block, system_prompt
from state import State

settings = Settings()


def _current_task(state: State) -> str:
    messages = [
        getattr(item, "text", item.content) for item in state.get("messages") or []
        if getattr(item, "type", None) == "human" and getattr(item, "text", item.content)
    ]
    return messages[-1] if messages else "Текущее расследование"


def _fallback_scope(state: State) -> InvestigationScope:
    task = _current_task(state)
    return InvestigationScope(
        mode="discovery",
        objective=task,
        included=[task],
        max_items=1,
    )


def _normalized_scope(scope: InvestigationScope) -> InvestigationScope:
    scope = scope.model_copy(deep=True)
    if scope.mode == "alert" and not scope.alert:
        scope.mode = "discovery"
        scope.max_items = min(scope.max_items, settings.scope_discovery_max_items)
    if scope.mode == "discovery":
        scope.max_items = min(scope.max_items, settings.scope_discovery_max_items)
        if not scope.sampling_policy:
            scope.sampling_policy = InvestigationScope.model_fields["sampling_policy"].default
    else:
        scope.max_items = min(scope.max_items, settings.planner_max_items)
    scope.max_items = max(1, scope.max_items)
    scope.included = list(dict.fromkeys(item.strip() for item in scope.included if item.strip()))[:5]
    if not scope.included:
        scope.included = [scope.objective]
    scope.optional = scope.optional[:3]
    return scope


def node_scope(state: State) -> dict:
    if state.get("investigation_scope"):
        return {}
    payload = {
        "task": _current_task(state),
        "intent": state.get("intent") or {},
        "active_skill": state.get("active_skill") or {},
        "scout_result": state.get("scout_result") or {},
        "current_result": state.get("inv_result") or {},
        "previous_report": state.get("report_result") or {},
        "previous_scope": state.get("investigation_scope") or {},
    }
    messages = [
        SystemMessage(content=system_prompt(
            compose("base", "scope"),
            context_block(state),
            structured_block("Данные для определения охвата", payload),
        )),
        HumanMessage(content="Сформируй InvestigationScope для текущего этапа."),
    ]
    messages = with_prompt_override(state, "scope", messages)
    audit_messages("scope", state, messages)
    try:
        response = llm_mod.scope_llm.invoke(messages, config=trace_config("scope", state))
        scope = response if isinstance(response, InvestigationScope) else InvestigationScope.model_validate(response)
    except Exception:
        scope = _fallback_scope(state)
    scope = _normalized_scope(scope)
    labels = {"discovery": "обзор", "targeted": "целевое расследование", "alert": "расследование алерта"}
    notification = AIMessage(
        content=(
            f"Готовлю {labels[scope.mode]}: {scope.objective}. "
            f"В текущем этапе — до {scope.max_items} проверок."
        ),
        name="scope",
    )
    return {
        "investigation_scope": scope.model_dump(),
        "progress": simple_progress("scope", f"Охват: {labels[scope.mode]}, до {scope.max_items} проверок"),
        "messages": [notification],
    }
