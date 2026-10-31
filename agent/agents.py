"""Ноды внешнего workflow и вложенные create_agent-агенты.

Tool-цикл каждого агента выполняется внутри create_agent. Наружу узлы
переносят только новые сообщения в приватный канал и дельты состояния;
в общий диалог попадают исключительно финальные ответы.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, ToolCallLimitMiddleware, dynamic_prompt
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    filter_messages,
)
from langgraph.types import Overwrite

import llm as llm_mod
from config import Settings
from prompts import (
    compose,
    context_block,
    critic_feedback_block,
    evidence_block,
    intent_hint_block,
    inv_summary_block,
    last_report_block,
    scout_block,
    structured_block,
    system_prompt,
)
from state import INV_MSGS, REP_MSGS, SCOUT_MSGS, AgentWorkState, State
from tools import investigator_tools, normalize_tool_result, reporter_tools, scout_tools

settings = Settings()

CRITIC_MSG_LIMIT = 2000
CRITIC_DIALOG_LIMIT = 8000


@dynamic_prompt
def _scout_prompt(request: ModelRequest) -> str:
    return system_prompt(compose("base", "scout"), context_block(request.state))


@dynamic_prompt
def _investigator_prompt(request: ModelRequest) -> str:
    state = request.state
    blocks = [context_block(state), scout_block(state), evidence_block(state), critic_feedback_block(state)]
    return system_prompt(compose("base", "investigator"), *blocks)


@dynamic_prompt
def _reporter_prompt(request: ModelRequest) -> str:
    state = request.state
    blocks: list[str] = [context_block(state), evidence_block(state)]
    if state.get("inv_result"):
        blocks.append(structured_block("Структурированный результат инвестигейтора", state["inv_result"]))
    elif state.get("inv_summary"):
        blocks.append(inv_summary_block(state["inv_summary"]))
    if state.get("last_report"):
        blocks.append(last_report_block(state["last_report"]))
    blocks.append(critic_feedback_block(state))
    return system_prompt(compose("base", "reporter"), *blocks)


_agents: dict = {}


def build_agents() -> None:
    """Пересобрать вложенные графы с текущей моделью (удобно и для тестов)."""
    global _agents
    _agents = {
        "scout": create_agent(
            model=llm_mod.llm,
            tools=scout_tools,
            middleware=[
                _scout_prompt,
                normalize_tool_result,
                ToolCallLimitMiddleware(run_limit=4, exit_behavior="end"),
            ],
            response_format=llm_mod.ScoutResult,
            state_schema=AgentWorkState,
            name="scout",
        ),
        "investigator": create_agent(
            model=llm_mod.llm,
            tools=investigator_tools,
            middleware=[
                _investigator_prompt,
                normalize_tool_result,
                ToolCallLimitMiddleware(run_limit=12, exit_behavior="end"),
            ],
            response_format=llm_mod.InvestigationResult,
            state_schema=AgentWorkState,
            name="investigator",
        ),
        "reporter": create_agent(
            model=llm_mod.llm,
            tools=reporter_tools,
            middleware=[
                _reporter_prompt,
                normalize_tool_result,
                ToolCallLimitMiddleware(run_limit=6, exit_behavior="end"),
            ],
            response_format=llm_mod.ReporterResult,
            state_schema=AgentWorkState,
            name="reporter",
        ),
    }


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
        if msg.name == agent:
            return msg
    return None


def _agent_final(
    state: State,
    response: AIMessage,
    agent: str,
    artifacts: list[str] | None = None,
) -> dict:
    """Финальный ответ (без tool_calls) — в общий диалог.

    Репортёр при доработке удаляет свой предыдущий ответ и дописывает
    новый в хвост. Артефакты прикрепляются к этому сообщению для UI.
    """
    out: list[BaseMessage] = []
    update: dict = {}
    final = AIMessage(content=response.content, name=agent)
    if agent == "reporter":
        prev = _find_prev_final(state, "reporter")
        if prev is not None:
            out.append(RemoveMessage(id=prev.id))
    final_artifacts = state.get("artifacts") if artifacts is None else artifacts
    if final_artifacts:
        final.artifacts = list(final_artifacts)
    out.append(final)
    update["messages"] = out
    return update


def _agent_convo_messages(state: State) -> list[BaseMessage]:
    """Пользователь и финальные ответы агентов, без служебных сообщений."""
    return filter_messages(
        state["messages"],
        include_types=["human"],
        include_names=["chat", "investigator", "reporter"],
    )


def _agent_input(state: State, work_key: str) -> dict:
    return {
        "messages": [
            *_agent_convo_messages(state),
            *(state.get(work_key) or []),
        ],
        "callback_url": state.get("callback_url") or "",
        "artifacts": list(state.get("artifacts") or []),
        "findings": list(state.get("findings") or []),
        "evidence": list(state.get("evidence") or []),
        "context": state.get("context") or {},
        "last_report": state.get("last_report") or {},
        "inv_summary": state.get("inv_summary") or "",
        "inv_result": state.get("inv_result") or {},
        "scout_note": state.get("scout_note") or "",
        "scout_result": state.get("scout_result") or {},
        "critic": state.get("critic") or {},
        "rework": state.get("rework") or 0,
    }


def _agent_schema(agent: str):
    return {
        "scout": llm_mod.ScoutResult,
        "investigator": llm_mod.InvestigationResult,
        "reporter": llm_mod.ReporterResult,
    }[agent]


def _structured_response(result: dict, schema=None) -> dict:
    value = result.get("structured_response")
    if value is not None:
        return value.model_dump() if hasattr(value, "model_dump") else dict(value)

    schemas = {
        llm_mod.ScoutResult.__name__: llm_mod.ScoutResult,
        llm_mod.InvestigationResult.__name__: llm_mod.InvestigationResult,
        llm_mod.ReporterResult.__name__: llm_mod.ReporterResult,
    }
    for message in reversed(result.get("messages") or []):
        for call in reversed(getattr(message, "tool_calls", None) or []):
            found = schemas.get(call.get("name"))
            if found is not None and (schema is None or found is schema):
                return found.model_validate(call["args"]).model_dump()

    content = next(
        (message.content for message in reversed(result.get("messages") or [])
         if isinstance(message, AIMessage) and isinstance(message.content, str) and message.content.strip()),
        None,
    )
    if content and schema is llm_mod.ScoutResult:
        return llm_mod.ScoutResult(note=content).model_dump()
    if content and schema is llm_mod.InvestigationResult:
        return llm_mod.InvestigationResult(
            summary=content,
            limitations=["Модель не вернула схемный ответ; использован обычный текст."],
        ).model_dump()
    if content and schema is llm_mod.ReporterResult:
        return llm_mod.ReporterResult(
            answer=content,
            limitations=["Модель не вернула схемный ответ; использован обычный текст."],
        ).model_dump()
    raise RuntimeError("create_agent не вернул structured_response")


async def _run_agent(agent: str, state: State, work_key: str) -> tuple[dict, list[BaseMessage], dict]:
    """Запустить create_agent и подготовить только дельты для внешнего State."""
    agent_input = _agent_input(state, work_key)
    schema = _agent_schema(agent)
    for attempt in range(2):
        result = await _agents[agent].ainvoke(agent_input)
        input_count = len(agent_input["messages"])
        new_messages = list(result["messages"][input_count:])
        try:
            _structured_response(result, schema)
            break
        except RuntimeError:
            if attempt == 1 or new_messages:
                raise

    update: dict = {}
    for key in ("artifacts", "findings", "evidence"):
        before = len(agent_input[key])
        values = list(result.get(key) or [])
        if len(values) > before:
            update[key] = values[before:]
    if result.get("last_report") != agent_input["last_report"]:
        update["last_report"] = result.get("last_report") or {}
    return result, new_messages, update


# ── Агенты ───────────────────────────────────────────────────────

async def node_scout(state: State) -> dict:
    """Скаут: tool-цикл внутри create_agent, наружу — короткий ориентир."""
    result, new_messages, update = await _run_agent("scout", state, SCOUT_MSGS)
    structured = _structured_response(result, llm_mod.ScoutResult)
    update[SCOUT_MSGS] = new_messages[:-1]
    update["scout_result"] = structured
    update["scout_note"] = structured["note"]
    return update


def node_chat(state: State) -> dict:
    """Чат-агент: простой вопрос без инструментов."""
    system = system_prompt(compose("base", "chat"), scout_block(state))
    convo = [SystemMessage(content=system), *_agent_convo_messages(state)]
    response = llm_mod.llm.invoke(convo)
    return _agent_final(state, response, "chat")


async def node_investigator(state: State) -> dict:
    """Инвестигейтор: завершает работу резюмем для критика и репортёра."""
    result, new_messages, update = await _run_agent("investigator", state, INV_MSGS)
    structured = _structured_response(result, llm_mod.InvestigationResult)
    final = AIMessage(content=structured["summary"], name="investigator")
    update[INV_MSGS] = [*new_messages[:-1], final]
    update["inv_result"] = structured
    update["inv_summary"] = structured["summary"]
    return update


async def node_reporter(state: State) -> dict:
    """Репортёр: создаёт или редактирует отчёт внутри create_agent."""
    result, new_messages, update = await _run_agent("reporter", state, REP_MSGS)
    structured = _structured_response(result, llm_mod.ReporterResult)
    update[REP_MSGS] = new_messages[:-1]
    update["report_result"] = structured
    update.update(_agent_final(
        state,
        AIMessage(content=structured["answer"]),
        "reporter",
        artifacts=list(result.get("artifacts") or []),
    ))
    return update


# ── Интент ───────────────────────────────────────────────────────

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
        "window": window.model_dump() if window else {"last_minutes": 60},
    }


_COMPLEX_WORDS = (
    "аномали", "расслед", "инцидент", "событи", "сколько", "почему",
    "разбери", "проанализируй", "сравни", "цифр",
)


def _intent_heuristic(state: State) -> dict:
    """Последний резерв: ключевые слова + состояние стейта."""
    base = {"window": {"last_minutes": 60}}
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


def node_intent(state: State) -> dict:
    """Выбрать маршрут и зафиксировать окно нового расследования."""
    decision = _intent_structured(state) or _intent_heuristic(state)
    update: dict = {"intent": decision}
    if decision.get("needs_new_data"):
        update["context"] = _new_context(decision)
        update["evidence"] = Overwrite([])
        update["findings"] = Overwrite([])
        update["last_report"] = {}
    return update


# ── Критик ───────────────────────────────────────────────────────

def _findings_block(state: State) -> str:
    findings = state.get("findings") or []
    if not findings:
        return "Записи расследования: отсутствуют."
    return structured_block("Записи расследования", {"items": findings})


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
    return _critic_run(
        state,
        "critic_evidence",
        llm_mod.critic_evidence_llm,
        ("ok", "rework_data"),
        extra or inv_summary_block(state.get("inv_summary") or "(резюме пока нет)"),
    )


def node_critic_report(state: State) -> dict:
    """Критик после репортёра: проверяет отчёт и выбирает доработку."""
    if state.get("last_report"):
        extra = last_report_block(state["last_report"])
    else:
        extra = structured_block("Финальный ответ репортёра", state.get("report_result"))
    return _critic_run(
        state,
        "critic_report",
        llm_mod.critic_report_llm,
        ("ok", "rework_report", "rework_data"),
        extra or "(ответа пока нет)",
    )
