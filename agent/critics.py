"""Структурированные критики доказательств и финального отчёта."""

from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

import llm as llm_mod
from config import Settings
from progress import agent_progress, done_progress
from prompts import (
    compose,
    context_block,
    evidence_block,
    inv_summary_block,
    last_report_block,
    structured_block,
    system_prompt,
)
from state import State

settings = Settings()

CRITIC_MSG_LIMIT = 2000
CRITIC_DIALOG_LIMIT = 8000


def _findings_block(state: State) -> str:
    findings = state.get("findings") or []
    if not findings:
        return "Записи расследования: отсутствуют."
    return structured_block("Записи расследования", {"items": findings})


def _intent_block(state: State) -> str:
    intent = state.get("intent") or {}
    if not intent.get("label"):
        return "Интент: не определён."
    line = f"Интент текущего запроса: {intent['label']}"
    if intent.get("rationale"):
        line += f" — {intent['rationale']}"
    return line


def _user_requests_block(state: State) -> str:
    users = [m for m in state.get("messages") or [] if isinstance(m, HumanMessage)]
    if not users:
        return "Сообщений пользователя нет."
    lines = []
    for i, msg in enumerate(users, start=1):
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        lines.append(f"{i}. {text[:CRITIC_MSG_LIMIT]}")
    return "Сообщения пользователя (в хронологии):\n" + "\n".join(lines)[:CRITIC_DIALOG_LIMIT]


def _critic_messages(state: State, prompt: str, extra_block: str) -> list[BaseMessage]:
    parts = [
        compose("base", prompt),
        _intent_block(state),
        _user_requests_block(state),
        context_block(state),
        evidence_block(state),
        _findings_block(state),
        extra_block,
    ]
    return [
        SystemMessage(content=system_prompt(*parts)),
        HumanMessage(content="Проверь работу по критериям из системного промпта."),
    ]


def _critic_run(state: State, prompt: str, model: Any, allowed_verdicts: tuple, extra_block: str) -> dict:
    """Один ход критика. Сбой критика не роняет run: дефолт — ok."""
    verdict, feedback = "ok", ""
    try:
        result = model.invoke(_critic_messages(state, prompt, extra_block))
        if result is not None:
            verdict = result.verdict if result.verdict in allowed_verdicts else "ok"
            feedback = getattr(result, "feedback", "") or ""
    except Exception:
        pass
    rework = state.get("rework", 0) + (0 if verdict == "ok" else 1)
    return {"critic": {"verdict": verdict, "feedback": feedback}, "rework": rework}


def node_critic_evidence(state: State) -> dict:
    """Критик после инвестигейтора: достаточно ли улик для отчёта."""
    extra = structured_block("Результат инвестигейтора", state.get("inv_result"))
    update = _critic_run(
        state,
        "critic_evidence",
        llm_mod.critic_evidence_llm,
        ("ok", "rework_data"),
        extra or inv_summary_block(state.get("inv_summary") or "(резюме пока нет)"),
    )
    if update["critic"]["verdict"] == "rework_data" and update["rework"] <= settings.max_rework:
        update["progress"] = agent_progress(state, "investigator", "Критик запросил дополнительные данные", "investigator")
        update["suggestions"] = []
    else:
        update["progress"] = agent_progress(state, "reporter", "Формирую ответ", "reporter")
    return update


def node_critic_report(state: State) -> dict:
    """Критик после репортёра: проверяет отчёт и выбирает доработку."""
    if state.get("last_report"):
        extra = last_report_block(state["last_report"])
    else:
        extra = structured_block("Финальный ответ репортёра", state.get("report_result"))
    update = _critic_run(
        state,
        "critic_report",
        llm_mod.critic_report_llm,
        ("ok", "rework_report", "rework_data"),
        extra or "(ответа пока нет)",
    )
    verdict = update["critic"]["verdict"]
    if verdict in ("rework_report", "rework_data") and update["rework"] <= settings.max_rework:
        agent = "reporter" if verdict == "rework_report" else "investigator"
        text = "Дорабатываю ответ" if agent == "reporter" else "Собираю дополнительные данные"
        update["progress"] = agent_progress(state, agent, text, agent)
        update["suggestions"] = []
    else:
        limitations = (state.get("report_result") or {}).get("limitations") or []
        update["progress"] = done_progress("Готово с ограничениями" if limitations else "Готово")
    return update
