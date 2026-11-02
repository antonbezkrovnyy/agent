"""Runtime вложенных create_agent-агентов и их узлы для внешнего workflow.

Tool-цикл каждого агента выполняется внутри create_agent. Наружу узлы
переносят только новые сообщения в приватный канал и дельты состояния;
в общий диалог попадают исключительно финальные ответы.
"""

import asyncio

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

import llm as llm_mod
from config import Settings
from prompts import (
    compose,
    context_block,
    critic_feedback_block,
    evidence_block,
    inv_summary_block,
    last_report_block,
    scout_block,
    structured_block,
    system_prompt,
)
from progress import TOOL_LIMITS, agent_progress, done_progress, report_suggestions
from state import INV_MSGS, REP_MSGS, SCOUT_MSGS, AgentWorkState, State
from tools import investigator_tools, normalize_tool_result, reporter_tools, scout_tools

settings = Settings()


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
                ToolCallLimitMiddleware(run_limit=TOOL_LIMITS["scout"], exit_behavior="end"),
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
                ToolCallLimitMiddleware(run_limit=TOOL_LIMITS["investigator"], exit_behavior="end"),
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
                ToolCallLimitMiddleware(run_limit=TOOL_LIMITS["reporter"], exit_behavior="end"),
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
    """Найти финальный ответ агента в текущем run для замены при доработке."""
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
    """Финальный ответ без tool_calls и необязательные артефакты для UI."""
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
        "progress": state.get("progress") or {},
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


def _timeout_result(schema, messages: list[BaseMessage]) -> dict:
    note = "Технический таймаут: этап остановлен из-за слишком долгого ответа."
    if schema is llm_mod.ScoutResult:
        structured = llm_mod.ScoutResult(note=note)
    elif schema is llm_mod.InvestigationResult:
        structured = llm_mod.InvestigationResult(summary=note, limitations=[note])
    else:
        structured = llm_mod.ReporterResult(answer=note, limitations=[note])
    return {
        "messages": [*messages, AIMessage(content=note)],
        "structured_response": structured,
    }


async def _run_agent(agent: str, state: State, work_key: str) -> tuple[dict, list[BaseMessage], dict]:
    """Запустить create_agent и подготовить только дельты для внешнего State."""
    agent_input = _agent_input(state, work_key)
    stage_text = {
        "scout": "Осматриваю журнал",
        "investigator": "Анализирую события",
        "reporter": "Формирую ответ",
    }[agent]
    agent_input["progress"] = agent_progress(
        {**state, "tool_usage": {}},
        agent,
        stage_text,
        agent,
    )
    schema = _agent_schema(agent)
    for attempt in range(2):
        try:
            result = await asyncio.wait_for(
                _agents[agent].ainvoke(agent_input),
                timeout=settings.agent_timeout_seconds,
            )
        except TimeoutError:
            result = _timeout_result(schema, agent_input["messages"])
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
    counts = result.get("run_tool_call_count") or {}
    usage = dict(state.get("tool_usage") or {})
    usage[agent] = counts.get("__all__", usage.get(agent, 0))
    update["tool_usage"] = usage
    return result, new_messages, update


async def node_scout(state: State) -> dict:
    """Скаут: tool-цикл внутри create_agent, наружу — короткий ориентир."""
    result, new_messages, update = await _run_agent("scout", state, SCOUT_MSGS)
    structured = _structured_response(result, llm_mod.ScoutResult)
    update[SCOUT_MSGS] = new_messages[:-1]
    update["scout_result"] = structured
    update["scout_note"] = structured["note"]
    progress_state = {**state, "tool_usage": update["tool_usage"]}
    update["progress"] = agent_progress(progress_state, "investigator", "Анализирую события", "investigator")
    return update


def node_chat(state: State) -> dict:
    """Чат-агент: простой вопрос без инструментов."""
    system = system_prompt(compose("base", "chat"), scout_block(state))
    convo = [SystemMessage(content=system), *_agent_convo_messages(state)]
    response = llm_mod.llm.invoke(convo)
    update = _agent_final(state, response, "chat")
    update["progress"] = done_progress()
    return update


async def node_investigator(state: State) -> dict:
    """Инвестигейтор: завершает работу резюмем для критика и репортёра."""
    result, new_messages, update = await _run_agent("investigator", state, INV_MSGS)
    structured = _structured_response(result, llm_mod.InvestigationResult)
    final = AIMessage(content=structured["summary"], name="investigator")
    update[INV_MSGS] = [*new_messages[:-1], final]
    update["inv_result"] = structured
    update["inv_summary"] = structured["summary"]
    progress_state = {**state, "tool_usage": update["tool_usage"]}
    if settings.critic_enabled:
        update["progress"] = agent_progress(progress_state, "critic_evidence", "Проверяю доказательства", "investigator")
    else:
        update["progress"] = agent_progress(progress_state, "reporter", "Формирую ответ", "reporter")
    return update


async def node_reporter(state: State) -> dict:
    """Репортёр: создаёт или редактирует отчёт внутри create_agent."""
    result, new_messages, update = await _run_agent("reporter", state, REP_MSGS)
    structured = _structured_response(result, llm_mod.ReporterResult)
    update[REP_MSGS] = new_messages[:-1]
    update["report_result"] = structured
    progress_state = {
        **state,
        "tool_usage": update["tool_usage"],
        "evidence": result.get("evidence") or state.get("evidence"),
    }
    update["suggestions"] = report_suggestions(
        progress_state,
        has_report=bool(result.get("last_report")),
    )
    if settings.critic_enabled:
        update["progress"] = agent_progress(progress_state, "critic_report", "Проверяю ответ", "reporter")
    else:
        update["progress"] = done_progress(
            "Готово с ограничениями" if structured.get("limitations") else "Готово"
        )
    update.update(_agent_final(
        state,
        AIMessage(content=structured["answer"]),
        "reporter",
        artifacts=list(result.get("artifacts") or []),
    ))
    return update
