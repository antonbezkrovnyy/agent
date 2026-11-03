"""Структурированные критики доказательств и финального отчёта."""

from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

import llm as llm_mod
from config import Settings
from contracts import CriticIssue
from evidence import claim_evidence_ids
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

CRITIC_MSG_LIMIT = settings.critic_message_limit
CRITIC_DIALOG_LIMIT = settings.critic_dialog_limit


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
        evidence_block(state, full=True, evidence_ids=claim_evidence_ids(state) or None),
        _findings_block(state),
        structured_block("Журнал гипотез", {"items": state.get("hypotheses") or []}),
        extra_block,
    ]
    return [
        SystemMessage(content=system_prompt(*parts)),
        HumanMessage(content="Проверь работу по критериям из системного промпта."),
    ]


def _deterministic_issues(state: State) -> list[dict]:
    records = {item.get("id"): item for item in state.get("evidence") or []}
    issues: list[CriticIssue] = []
    for result_key in ("inv_result", "report_result"):
        for claim in (state.get(result_key) or {}).get("claims") or []:
            unknown = [item for item in claim.get("evidence_ids") or [] if item not in records]
            if unknown:
                issues.append(CriticIssue(
                    claim=claim.get("text") or "",
                    evidence_ids=unknown,
                    reason="Утверждение ссылается на отсутствующие Evidence ID.",
                    suggested_action="rework_report",
                ))
    for hypothesis in state.get("hypotheses") or []:
        evidence_ids = hypothesis.get("evidence_ids") or []
        unknown = [item for item in evidence_ids if item not in records]
        if unknown or (hypothesis.get("status") != "open" and not evidence_ids):
            issues.append(CriticIssue(
                claim=hypothesis.get("text") or "",
                evidence_ids=unknown,
                reason="Статус гипотезы не подтверждён существующими Evidence.",
                suggested_action="rework_data",
            ))
    used = [records[item] for item in claim_evidence_ids(state) if item in records]
    for record in used:
        window = record.get("window") or {}
        bounds = [window.get("from"), window.get("to")]
        if any(bounds) and not all(bounds):
            issues.append(CriticIssue(
                evidence_ids=[record["id"]],
                reason="Evidence содержит неполное временное окно.",
                suggested_action="rework_data",
            ))
    limitations = [
        item
        for key in ("inv_result", "report_result")
        for item in (state.get(key) or {}).get("limitations") or []
    ]
    degraded = [item for item in used if item.get("status") != "ok"]
    if degraded and not limitations:
        issues.append(CriticIssue(
            evidence_ids=[item["id"] for item in degraded],
            reason="Partial/error Evidence использованы без явных limitations.",
            suggested_action="rework_report",
        ))
    unique = {}
    for issue in issues:
        key = (issue.claim, tuple(issue.evidence_ids), issue.reason)
        unique[key] = issue.model_dump()
    return list(unique.values())


def _critic_run(state: State, prompt: str, model: Any, allowed_verdicts: tuple, extra_block: str) -> dict:
    """Детерминированные проверки и один LLM-ход критика."""
    verdict, feedback = "critic_unavailable", "Критик не вернул корректный вердикт."
    deterministic = _deterministic_issues(state)
    issues = list(deterministic)
    try:
        result = model.invoke(_critic_messages(state, prompt, extra_block))
        if result is not None and result.verdict in allowed_verdicts:
            verdict = result.verdict
            feedback = getattr(result, "feedback", "") or ""
            for issue in getattr(result, "issues", []) or []:
                payload = issue.model_dump() if hasattr(issue, "model_dump") else dict(issue)
                if payload not in issues:
                    issues.append(payload)
    except Exception as exc:
        feedback = f"Критик недоступен: {type(exc).__name__}."
    if deterministic and verdict != "critic_unavailable":
        if "rework_data" in allowed_verdicts and any(
            item["suggested_action"] == "rework_data" for item in deterministic
        ):
            verdict = "rework_data"
        elif "rework_report" in allowed_verdicts:
            verdict = "rework_report"
        elif "rework_data" in allowed_verdicts:
            verdict = "rework_data"
        if not feedback:
            feedback = " ".join(item["reason"] for item in deterministic)
    rework = state.get("rework", 0) + (1 if verdict.startswith("rework_") else 0)
    return {"critic": {"verdict": verdict, "feedback": feedback, "issues": issues}, "rework": rework}


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
        text = "Готово с ограничениями" if limitations else "Готово"
        if verdict == "critic_unavailable":
            text = "Готово без проверки критика"
        update["progress"] = done_progress(text)
    return update
