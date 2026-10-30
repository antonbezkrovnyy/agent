"""Ноды агентов.

Каждый агент — LLM со своим системным промптом (prompts/*.md)
и своим набором тулз (tools.py). Ноды не знают о графе:
получают State, возвращают дикт обновлений.
"""

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

import llm as llm_mod
from prompts import (
    compose,
    critic_feedback_block,
    intent_hint_block,
    last_report_block,
    system_prompt,
)
from state import State
from tools import (
    chat_tools,
    investigator_tools,
    reporter_tools,
    scout_tools,
)

# Сколько символов из findings и хвоста диалога давать критику
CRITIC_FINDINGS_LIMIT = 6000
CRITIC_TAIL_MESSAGES = 4
CRITIC_MSG_LIMIT = 1500


def _agent_invoke(state: State, prompt: str, tools: list, extra_blocks: list[str]) -> AIMessage:
    """Один ход LLM-агента: системный промпт + история диалога + тулзы."""
    system = system_prompt(compose("base", prompt), *extra_blocks)
    messages: list[BaseMessage] = [SystemMessage(content=system), *state["messages"]]
    model = llm_mod.llm.bind_tools(tools) if tools else llm_mod.llm
    return model.invoke(messages)


def _finalize(response: AIMessage, state: State) -> dict:
    """Финальный ответ (без tool_calls) забирает артефакты из State —
    так data-URL отчётов доходит до чекпоинта и UI."""
    if not getattr(response, "tool_calls", None) and state.get("artifacts"):
        response.artifacts = list(state["artifacts"])
    return {"messages": [response]}


# ── Агенты ───────────────────────────────────────────────────────

def node_scout(state: State) -> dict:
    """Скаут: 1–2 быстрых запроса в MCP и короткий ориентир для всех.

    Считает ветку готовой (ready +1) только на финальном ответе —
    пока скаут крутит tool-цикл, барьер не должен пускать агентов.
    """
    response = _agent_invoke(state, "scout", scout_tools, [])
    update = _finalize(response, state)
    update["ready"] = 0 if response.tool_calls else 1
    return update


def node_chat(state: State) -> dict:
    """Чат-агент: простой вопрос без инструментов."""
    response = _agent_invoke(state, "chat", chat_tools, [])
    return _finalize(response, state)


def node_investigator(state: State) -> dict:
    """Инвестигейтор: MCP + log_finding; завершается резюме."""
    response = _agent_invoke(state, "investigator", investigator_tools, [critic_feedback_block(state)])
    return _finalize(response, state)


def node_reporter(state: State) -> dict:
    """Репортёр: отчёты и их правки; видит последний отчёт в промпте."""
    blocks: list[str] = []
    if state.get("last_report"):
        blocks.append(last_report_block(state["last_report"]))
    blocks.append(critic_feedback_block(state))
    response = _agent_invoke(state, "reporter", reporter_tools, blocks)
    return _finalize(response, state)


# ── Интент ───────────────────────────────────────────────────────

_INTENT_LABELS = ("simple", "complex", "continuation")


def _intent_messages(state: State) -> list[BaseMessage]:
    system = system_prompt(compose("base", "intent"), intent_hint_block(state))
    return [SystemMessage(content=system), *state["messages"]]


def _last_human_text(state: State) -> str:
    for msg in reversed(state.get("messages") or []):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            return msg.content
    return ""


def _intent_structured(state: State) -> dict | None:
    """Основной путь: структурированный вывод через function_calling."""
    try:
        decision = llm_mod.intent_llm.invoke(_intent_messages(state))
    except Exception:
        return None
    if decision is None or not getattr(decision, "intent", None):
        return None
    label = decision.intent
    if label not in _INTENT_LABELS:
        return None
    return {"label": label, "rationale": getattr(decision, "rationale", "")}


def _intent_json(state: State) -> dict | None:
    """Резерв: обычный вызов с просьбой ответить JSON, парсим ответ."""
    try:
        messages = _intent_messages(state)
        messages.append(HumanMessage(
            content='Ответь строго JSON: {"intent": "simple|complex|continuation", "rationale": "..."}',
        ))
        response = llm_mod.llm.invoke(messages)
        text = response.content if isinstance(response.content, str) else str(response.content)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        data = json.loads(match.group())
        label = data.get("intent")
        if label not in _INTENT_LABELS:
            return None
        return {"label": label, "rationale": str(data.get("rationale", ""))}
    except Exception:
        return None


_COMPLEX_WORDS = (
    "аномали", "расслед", "инцидент", "событи", "сколько", "почему",
    "разбери", "проанализируй", "сравни", "цифр",
)


def _intent_heuristic(state: State) -> dict:
    """Последний резерв: ключевые слова + состояние стейта."""
    if state.get("last_report") or state.get("findings"):
        return {"label": "continuation", "rationale": "в стейте есть отчёт/записи — продолжаем"}
    last = _last_human_text(state).lower()
    if any(word in last for word in _COMPLEX_WORDS):
        return {"label": "complex", "rationale": "похоже на запрос данных из журнала"}
    return {"label": "simple", "rationale": "нет признаков анализа данных"}


def node_intent(state: State) -> dict:
    """Классифицирует последнее сообщение: simple / complex / continuation.

    Ветка считается готовой сразу — у интента нет tool-цикла.
    """
    decision = _intent_structured(state) or _intent_json(state) or _intent_heuristic(state)
    return {"intent": decision, "ready": 1}


# ── Критик ───────────────────────────────────────────────────────

def _findings_block(state: State) -> str:
    findings = state.get("findings") or []
    if not findings:
        return "Записи расследования: отсутствуют."
    text = "\n".join(f"{i + 1}. {note}" for i, note in enumerate(findings))
    return "Записи расследования:\n" + text[:CRITIC_FINDINGS_LIMIT]


def _dialog_tail_block(state: State) -> str:
    """Хвост диалога без tool-сообщений: резюме инвестигейтора, ответы."""
    tail = [m for m in state.get("messages") or [] if not isinstance(m, ToolMessage)][-CRITIC_TAIL_MESSAGES:]
    lines = []
    for msg in tail:
        role = "пользователь" if isinstance(msg, HumanMessage) else "агент"
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"{role}: {content[:CRITIC_MSG_LIMIT]}")
    return "Хвост диалога:\n" + "\n".join(lines)


def _critic_messages(state: State, prompt: str) -> list[BaseMessage]:
    parts: list[str] = [
        compose("base", prompt),
        _findings_block(state),
        _dialog_tail_block(state),
    ]
    if state.get("last_report"):
        parts.append(last_report_block(state["last_report"]))
    task = _last_human_text(state)
    return [
        SystemMessage(content=system_prompt(*parts)),
        HumanMessage(content=f"Задача пользователя: {task}"),
    ]


def _critic_run(state: State, prompt: str, model: Any, allowed_verdicts: tuple[str, ...]) -> dict:
    """Один ход критика. Сбой критика не роняет run: дефолт — ok."""
    verdict, feedback = "ok", ""
    try:
        result = model.invoke(_critic_messages(state, prompt))
        if result is not None:
            verdict = result.verdict if result.verdict in allowed_verdicts else "ok"
            feedback = getattr(result, "feedback", "") or ""
    except Exception:
        pass
    rework = state.get("rework", 0) + (0 if verdict == "ok" else 1)
    return {"critic": {"verdict": verdict, "feedback": feedback}, "rework": rework}


def node_critic_evidence(state: State) -> dict:
    """Критик после инвестигейтора: достаточно ли улик для отчёта."""
    return _critic_run(state, "critic_evidence", llm_mod.critic_evidence_llm, ("ok", "rework_data"))


def node_critic_report(state: State) -> dict:
    """Критик после репортёра: отчёт противоречит ли записям."""
    return _critic_run(state, "critic_report", llm_mod.critic_report_llm, ("ok", "rework_report"))
