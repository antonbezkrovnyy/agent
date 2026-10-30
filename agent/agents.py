"""Ноды агентов.

Каждый агент — LLM со своим системным промптом (prompts/*.md) и своим
набором тулз (tools.py). Tool-цикл агента (сообщения с tool_calls и их
ответы) крутится в приватном канале стейта — в общий диалог messages
попадают только служебные сообщения переходов и финальный ответ.

Ноды не знают о графе: получают State, возвращают дикт обновлений.
"""

import json
import re
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)

import llm as llm_mod
from config import Settings
from prompts import (
    compose,
    critic_feedback_block,
    intent_hint_block,
    inv_summary_block,
    last_report_block,
    scout_block,
    system_prompt,
)
from state import INV_MSGS, REP_MSGS, SCOUT_MSGS, State
from tools import investigator_tools, reporter_tools, scout_tools

settings = Settings()

# Служебные сообщения в диалог: переходы, а не ответы агентов
SVC_START = "Определяем интент и запускаем скаута"
SVC_EVIDENCE = "Отчёт инвестигейтора передан критику"
SVC_REPORT = "Отчёт передан критику"
SVC_REWORK_REPORT = "Отчёт отправлен на доработку"
SVC_REWORK_DATA = "Отчёт отправлен инвестигейтору: не хватает данных"

# Сколько символов давать критику
CRITIC_FINDINGS_LIMIT = 6000
CRITIC_MSG_LIMIT = 2000
CRITIC_DIALOG_LIMIT = 8000


# ── Сообщения в диалог ───────────────────────────────────────────

def _service(text: str) -> AIMessage:
    """Служебное сообщение перехода в общий диалог."""
    return AIMessage(content=text)


def _combined_message(state: State) -> AIMessage:
    """Объединённое сообщение «интент + скаут»: одно вместо двух веток."""
    intent = state.get("intent") or {}
    line = f"Интент: {intent.get('label') or 'не определён'}"
    if intent.get("rationale"):
        line += f" — {intent['rationale']}"
    note = (state.get("scout_note") or "").strip()
    if note:
        # Скаут обычно начинает ответ «Скаут:» — префикс не дублируем
        if note.lower().startswith("скаут:"):
            note = note[len("Скаут:"):].strip()
        line += f"\nСкаут: {note}"
    return AIMessage(content=line)


def _intro_messages(state: State) -> list[AIMessage]:
    """Первый финализирующий агент run'а дописывает в диалог
    объединённое сообщение «интент + скаут»; остальные — нет."""
    if not state.get("intro") and (state.get("intent") or {}).get("label"):
        return [_combined_message(state)]
    return []


def _last_human_index(messages: list) -> int:
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return i
    return -1


def _find_prev_final(state: State, agent: str) -> AIMessage | None:
    """Финальный ответ агента в текущем run'е (после последнего сообщения
    пользователя): доработка переписывает его, а не добавляет второй ответ."""
    msgs = list(state["messages"])
    start = _last_human_index(msgs) + 1
    for msg in reversed(msgs[start:]):
        if getattr(msg, "agent_name", None) == agent:
            return msg
    return None


def _agent_final(state: State, response: AIMessage, agent: str) -> dict:
    """Финальный ответ (без tool_calls) — в общий диалог.

    Первый агент run'а дописывает объединённое сообщение «интент +
    скаут». Репортёр при доработке удаляет свой предыдущий ответ
    (RemoveMessage по id) и дописывает новый в хвост диалога — в
    диалоге остаётся один финальный, и он последний. Ответ забирает
    артефакты из стейта — так data-URL отчётов доходит до чекпоинта
    и UI.
    """
    out: list[BaseMessage] = _intro_messages(state)
    update: dict = {}
    if out:
        update["intro"] = 1
    final = AIMessage(content=response.content)
    final.agent_name = agent
    if agent == "reporter":
        prev = _find_prev_final(state, "reporter")
        if prev is not None:
            out.append(RemoveMessage(id=prev.id))
    if state.get("artifacts"):
        final.artifacts = list(state["artifacts"])
    out.append(final)
    update["messages"] = out
    return update


def _agent_invoke(state: State, prompt: str, tools: list, blocks: list[str], work_key: str) -> AIMessage:
    """Один ход LLM-агента: системный промпт + диалог + свой tool-цикл."""
    system = system_prompt(compose("base", prompt), *blocks)
    convo: list[BaseMessage] = [
        SystemMessage(content=system),
        *state["messages"],
        *(state.get(work_key) or []),
    ]
    model = llm_mod.llm.bind_tools(tools) if tools else llm_mod.llm
    return model.invoke(convo)


# ── Агенты ───────────────────────────────────────────────────────

def node_scout(state: State) -> dict:
    """Скаут: 1–2 быстрых запроса в MCP, в конце — короткий ориентир.

    В общий диалог не пишет: ориентир уходит в scout_note и попадёт в
    объединённое сообщение, которое допишет первый агент run'а.
    """
    response = _agent_invoke(state, "scout", scout_tools, [], SCOUT_MSGS)
    if response.tool_calls:
        return {SCOUT_MSGS: [response]}
    return {"scout_note": response.content}


def node_chat(state: State) -> dict:
    """Чат-агент: простой вопрос без инструментов."""
    response = _agent_invoke(state, "chat", [], [scout_block(state)], "")
    return _agent_final(state, response, "chat")


def node_investigator(state: State) -> dict:
    """Инвестигейтор: MCP + log_finding; завершается резюме (в inv_summary).

    Резюме — промежуточный результат: в общий диалог не попадает (его
    увидят критик и репортёр через inv_summary), остаётся в приватном
    канале — так история tool-цикла не рвётся при доработке.
    """
    blocks = [scout_block(state), critic_feedback_block(state)]
    response = _agent_invoke(state, "investigator", investigator_tools, blocks, INV_MSGS)
    if response.tool_calls:
        return {INV_MSGS: [response]}
    update: dict = {INV_MSGS: [response], "inv_summary": response.content}
    intro = _intro_messages(state)
    if intro:
        update["messages"] = intro
        update["intro"] = 1
    return update


def node_reporter(state: State) -> dict:
    """Репортёр: отчёты и их правки; видит резюме инвестигейтора и
    последний отчёт в промпте."""
    blocks: list[str] = []
    if state.get("inv_summary"):
        blocks.append(inv_summary_block(state["inv_summary"]))
    if state.get("last_report"):
        blocks.append(last_report_block(state["last_report"]))
    blocks.append(critic_feedback_block(state))
    response = _agent_invoke(state, "reporter", reporter_tools, blocks, REP_MSGS)
    if response.tool_calls:
        return {REP_MSGS: [response]}
    return _agent_final(state, response, "reporter")


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

    Результат — в канал intent; в диалог интент не пишет.
    """
    decision = _intent_structured(state) or _intent_json(state) or _intent_heuristic(state)
    return {"intent": decision}


# ── Критик ───────────────────────────────────────────────────────

def _findings_block(state: State) -> str:
    findings = state.get("findings") or []
    if not findings:
        return "Записи расследования: отсутствуют."
    text = "\n".join(f"{i + 1}. {note}" for i, note in enumerate(findings))
    return "Записи расследования:\n" + text[:CRITIC_FINDINGS_LIMIT]


def _intent_block(state: State) -> str:
    """Сохранинный интент — чтобы критик понимал, чего хочет пользователь."""
    intent = state.get("intent") or {}
    if not intent.get("label"):
        return "Интент: не определён."
    line = f"Интент текущего запроса: {intent['label']}"
    if intent.get("rationale"):
        line += f" — {intent['rationale']}"
    return line


def _user_requests_block(state: State) -> str:
    """Все сообщения пользователя в хронологии — не только последнее."""
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
    update = _critic_run(
        state,
        "critic_evidence",
        llm_mod.critic_evidence_llm,
        ("ok", "rework_data"),
        inv_summary_block(state.get("inv_summary") or "(резюме пока нет)"),
    )
    update["messages"] = [_service(SVC_EVIDENCE)]
    if update["critic"]["verdict"] == "rework_data" and update["rework"] <= settings.max_rework:
        update["messages"].append(_service("Инвестигейтор доищет данные по замечаниям критика"))
    return update


def node_critic_report(state: State) -> dict:
    """Критик после репортёра: отчёт противоречит ли записям,
    хватает ли данных, не пора ли отправить инвестигейтора.

    В диалог пишет только при решении о доработке — иначе финальный
    ответ репортёра остаётся последним сообщением.
    """
    update = _critic_run(
        state,
        "critic_report",
        llm_mod.critic_report_llm,
        ("ok", "rework_report", "rework_data"),
        last_report_block(state["last_report"]) if state.get("last_report") else "(отчёта пока нет)",
    )
    verdict = update["critic"]["verdict"]
    if verdict != "ok" and update["rework"] <= settings.max_rework:
        update["messages"] = [
            _service(SVC_REPORT),
            _service(SVC_REWORK_REPORT if verdict == "rework_report" else SVC_REWORK_DATA),
        ]
    return update
