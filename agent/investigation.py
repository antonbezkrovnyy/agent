"""Классификация интента, контекст и смена текущего расследования."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.types import Overwrite, interrupt

import llm as llm_mod
from agents import _agent_convo_messages
from progress import agent_progress, done_progress, simple_progress
from prompts import compose, intent_hint_block, system_prompt
from reporting import build_report
from state import State


def _intent_messages(state: State) -> list[BaseMessage]:
    system = system_prompt(compose("base", "intent"), intent_hint_block(state))
    return [SystemMessage(content=system), *_agent_convo_messages(state)]


def _last_human_text(state: State) -> str:
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            return msg.content
    return ""


def _intent_structured(state: State) -> dict | None:
    """Structured output с настроенным fallback на JSON mode."""
    try:
        decision = llm_mod.intent_llm.invoke(_intent_messages(state))
    except Exception:
        return None
    if decision is None or not getattr(decision, "intent", None):
        return None
    window = getattr(decision, "window", None)
    return {
        "label": decision.intent,
        "rationale": getattr(decision, "rationale", ""),
        "needs_new_data": getattr(decision, "needs_new_data", False),
        "edit_existing_report": getattr(decision, "edit_existing_report", False),
        "export_data": getattr(decision, "export_data", False),
        "new_investigation": getattr(decision, "new_investigation", "continue"),
        "window": window.model_dump() if window else {"last_minutes": 60},
    }


_COMPLEX_WORDS = (
    "аномали", "расслед", "инцидент", "событи", "сколько", "почему",
    "разбери", "проанализируй", "сравни", "цифр",
)


def _intent_heuristic(state: State) -> dict:
    """Последний резерв: ключевые слова + состояние стейта."""
    base = {"window": {"last_minutes": 60}, "new_investigation": "continue"}
    if state.get("last_report"):
        return {
            **base,
            "label": "continuation",
            "rationale": "в стейте есть отчёт — продолжаем",
            "needs_new_data": False,
            "edit_existing_report": True,
        }
    if state.get("findings"):
        return {
            **base,
            "label": "continuation",
            "rationale": "в стейте есть записи — продолжаем",
            "needs_new_data": True,
            "edit_existing_report": False,
        }
    last = _last_human_text(state).lower()
    if any(word in last for word in _COMPLEX_WORDS):
        return {
            **base,
            "label": "complex",
            "rationale": "похоже на запрос данных из журнала",
            "needs_new_data": True,
            "edit_existing_report": False,
            "new_investigation": "new",
        }
    return {
        **base,
        "label": "simple",
        "rationale": "нет признаков анализа данных",
        "needs_new_data": False,
        "edit_existing_report": False,
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
    return {
        "id": uuid4().hex,
        "time_from": start.isoformat(),
        "time_to": end.isoformat(),
        "timezone": "UTC",
    }


def _work_progress(state: State, decision: dict) -> dict:
    if decision.get("export_data"):
        return simple_progress("export", "Готовлю полную выгрузку")
    if decision.get("needs_new_data"):
        if decision.get("label") == "continuation" and state.get("context"):
            return agent_progress(state, "investigator", "Продолжаю расследование", "investigator")
        return agent_progress(state, "scout", "Осматриваю журнал", "scout")
    if decision.get("label") == "simple":
        return simple_progress("chat", "Формирую ответ")
    return agent_progress(state, "reporter", "Формирую ответ", "reporter")


def node_intent(state: State) -> dict:
    """Зафиксировать маршрут и окно; смену расследования подтверждает пользователь."""
    decision = _intent_structured(state) or _intent_heuristic(state)
    update = {
        "intent": decision,
        "transition": decision.get("new_investigation") or "continue",
    }
    if not state.get("context") and (decision.get("needs_new_data") or decision.get("export_data")):
        update["context"] = _new_context(decision)
    has_history = bool(state.get("evidence") or state.get("findings") or state.get("last_report"))
    if has_history and update["transition"] in ("new", "uncertain"):
        update["progress"] = simple_progress("confirm", "Нужен ваш выбор", "waiting")
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
    message = AIMessage(content="Прежний отчёт сохранён файлом.", name="archive")
    message.artifacts = [url]
    return {"archive_done": 1, "artifacts": [url], "messages": [message]}


def node_archive(state: State) -> dict:
    update = _archive_update(state)
    if state.get("transition") == "new_thread":
        messages = list(update.get("messages") or [])
        messages.append(AIMessage(
            content="Прежний отчёт сохранён. Откройте новый чат для нового расследования.",
            name="reporter",
        ))
        update["messages"] = messages
        update["progress"] = done_progress("Расследование сохранено")
        update["suggestions"] = []
    elif state.get("transition") == "new":
        update.update({
            "context": _new_context(state["intent"]),
            "evidence": Overwrite([]),
            "findings": Overwrite([]),
            "last_report": {},
            "inv_summary": "",
        })
        update["progress"] = _work_progress({**state, **update}, state["intent"])
    return update


def node_confirm_investigation(state: State) -> dict:
    answer = interrupt({
        "type": "choice",
        "question": "Начать новое расследование или продолжить текущее?",
        "options": [
            {"label": "Продолжить текущее", "value": "continue"},
            {"label": "Начать новое здесь", "value": "new"},
            {"label": "Начать новый чат", "value": "new_thread"},
        ],
    })
    update: dict = {"transition": answer if answer in ("new", "new_thread") else "continue"}
    if answer == "continue" and not state.get("context"):
        update["context"] = _new_context(state["intent"])
    if update["transition"] in ("new", "new_thread"):
        update["progress"] = simple_progress("archive", "Сохраняю прежний отчёт")
    else:
        update["progress"] = _work_progress({**state, **update}, state["intent"])
    return update
