"""Runtime вложенных create_agent-агентов и их узлы для внешнего workflow.

Tool-цикл каждого агента выполняется внутри create_agent. Наружу узлы
переносят только новые сообщения в приватный канал и дельты состояния;
в общий диалог попадают исключительно финальные ответы.
"""

import asyncio
from datetime import UTC, datetime

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelRequest,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
    dynamic_prompt,
    wrap_model_call,
)
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    filter_messages,
)

import llm as llm_mod
from artifact_store import artifact_url
from config import Settings
from contracts import json_dumps
from observability import trace_config
from evidence import chain_block
from execution_audit import audit_messages
from control_overrides import effective_prompt, with_prompt_override
from step_mode import ToolStepMiddleware
from prompts import (
    compose,
    context_block,
    critic_feedback_block,
    evidence_block,
    inv_summary_block,
    last_report_block,
    memory_block,
    scope_block,
    scout_block,
    skill_block,
    structured_block,
    system_prompt,
    work_item_block,
    work_items_block,
)
from progress import TOOL_LIMITS, agent_progress, done_progress, report_suggestions, simple_progress
from skills import get_skill
from state import INV_MSGS, REP_MSGS, SCOUT_MSGS, AgentWorkState, State
from tools import investigator_tools, normalize_tool_result, reporter_tools, scout_tools

settings = Settings()


_FINALIZE_TOOLS = {
    "scout": {"log_finding", "ScoutResult"},
    "investigator": {
        "log_finding",
        "list_findings",
        "get_finding",
        "get_evidence",
        "get_evidence_batch",
        "list_hypotheses",
        "get_hypothesis",
        "update_hypothesis",
        "InvestigationResult",
    },
    "reporter": {
        "list_findings",
        "get_finding",
        "get_evidence_batch",
        "save_report",
        "send_report",
        "ReporterResult",
    },
}


def _finalize_tool_filter(role: str):
    @wrap_model_call
    async def middleware(request, handler):
        if request.state.get("agent_phase") != "finalize":
            return await handler(request)
        allowed = _FINALIZE_TOOLS[role]
        tools = [item for item in request.tools if getattr(item, "name", "") in allowed]
        return await handler(request.override(tools=tools))

    return middleware


def _history_middleware():
    @wrap_model_call
    async def normalize_history(request, handler):
        return await handler(request.override(
            messages=_structured_history_messages(request.messages)
        ))

    return normalize_history


def _middleware(role: str, prompt):
    return [
        SummarizationMiddleware(
            model=llm_mod.model_for_role(role),
            trigger=("tokens", settings.summarization_trigger_tokens),
            keep=("messages", settings.summarization_keep_messages),
        ),
        prompt,
        _history_middleware(),
        _finalize_tool_filter(role),
        normalize_tool_result,
        ToolStepMiddleware(role),
        ToolCallLimitMiddleware(run_limit=TOOL_LIMITS[role], exit_behavior="end"),
    ]


def _audited_prompt(role: str, request: ModelRequest, prompt: str) -> str:
    prompt = effective_prompt(request.state, role, prompt)
    if request.state.get("agent_phase") == "finalize":
        prompt += (
            "\n\nТехническая фаза завершения. Не запрашивай новые данные. "
            "Используй только разрешённые инструменты чтения и фиксации результатов, "
            "затем верни structured response с ограничениями и NOT_CHECKED."
        )
    audit_messages(role, request.state, [SystemMessage(content=prompt), *request.messages], request.tools)
    return prompt


@dynamic_prompt
def _scout_prompt(request: ModelRequest) -> str:
    prompt = system_prompt(compose("base", "scout"), context_block(request.state))
    return _audited_prompt("scout", request, prompt)


@dynamic_prompt
def _investigator_prompt(request: ModelRequest) -> str:
    from prompts import continuation_block

    state = request.state
    blocks = [
        scope_block(state),
        work_item_block(state),
        continuation_block(state),
        context_block(state),
        scout_block(state),
        skill_block(state),
        evidence_block(state),
        structured_block("Журнал гипотез", {"items": state.get("hypotheses") or []}),
        memory_block(state),
        critic_feedback_block(state),
    ]
    return _audited_prompt("investigator", request, system_prompt(compose("base", "investigator"), *blocks))


@dynamic_prompt
def _reporter_prompt(request: ModelRequest) -> str:
    state = request.state
    blocks: list[str] = [scope_block(state), work_items_block(state), context_block(state), scout_block(state), evidence_block(state), chain_block(state)]
    if state.get("inv_result"):
        blocks.append(structured_block("Структурированный результат инвестигейтора", state["inv_result"]))
    elif state.get("inv_summary"):
        blocks.append(inv_summary_block(state["inv_summary"]))
    if state.get("last_report"):
        blocks.append(last_report_block(state["last_report"]))
    blocks.append(memory_block(state))
    blocks.append(critic_feedback_block(state))
    return _audited_prompt("reporter", request, system_prompt(compose("base", "reporter"), *blocks))


_agents: dict = {}


def build_agents() -> None:
    """Пересобрать вложенные графы с текущей моделью (удобно и для тестов)."""
    global _agents
    _agents = {
        "scout": create_agent(
            model=llm_mod.model_for_role("scout"),
            tools=scout_tools,
            middleware=_middleware("scout", _scout_prompt),
            response_format=llm_mod.ScoutResult,
            state_schema=AgentWorkState,
            name="scout",
        ),
        "investigator": create_agent(
            model=llm_mod.model_for_role("investigator"),
            tools=investigator_tools,
            middleware=_middleware("investigator", _investigator_prompt),
            response_format=llm_mod.InvestigationResult,
            state_schema=AgentWorkState,
            name="investigator",
        ),
        "reporter": create_agent(
            model=llm_mod.model_for_role("reporter"),
            tools=reporter_tools,
            middleware=_middleware("reporter", _reporter_prompt),
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
    artifacts: list[dict] | None = None,
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
        final.artifacts = [artifact_url(item) for item in final_artifacts]
    out.append(final)
    update["messages"] = out
    return update


def _public_notifications(messages: list[BaseMessage]) -> list[AIMessage]:
    return [
        item for item in messages
        if isinstance(item, AIMessage) and item.name in ("finding", "window", "hypothesis")
    ]


_STRUCTURED_RESULT_TOOLS = {"ScoutResult", "InvestigationResult", "ReporterResult"}


def _structured_history_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    sanitized = []
    for index, message in enumerate(messages):
        calls = getattr(message, "tool_calls", []) or []
        structured = [call for call in calls if call.get("name") in _STRUCTURED_RESULT_TOOLS]
        remaining_calls = [call for call in calls if call.get("name") not in _STRUCTURED_RESULT_TOOLS]
        if structured:
            content = json_dumps([
                {"name": call["name"], "arguments": call.get("args") or {}}
                for call in structured
            ], ensure_ascii=False)
            message = message.model_copy(update={
                "content": content,
                "tool_calls": remaining_calls,
                "invalid_tool_calls": [],
            })
        sanitized.append(message)
        for call in remaining_calls:
            call_id = call.get("id")
            if not call_id:
                continue
            paired = any(
                isinstance(candidate, ToolMessage) and candidate.tool_call_id == call_id
                for candidate in messages[index + 1:]
            )
            if not paired:
                sanitized.append(ToolMessage(
                    content=json_dumps({
                        "ok": False,
                        "error": "Tool result is not present in preserved agent history.",
                        "history_incomplete": True,
                    }, ensure_ascii=False),
                    tool_call_id=call_id,
                    name=call.get("name") or "unknown_tool",
                    status="error",
                ))
    ordered = []
    notifications = []
    for message in sanitized:
        if isinstance(message, AIMessage) and message.name in ("finding", "window", "hypothesis"):
            notifications.append(message)
            continue
        if notifications and not isinstance(message, ToolMessage):
            ordered.extend(notifications)
            notifications.clear()
        ordered.append(message)
    ordered.extend(notifications)
    return ordered


def _agent_convo_messages(state: State) -> list[BaseMessage]:
    """Пользователь и финальные ответы агентов, без служебных сообщений."""
    return filter_messages(
        state["messages"],
        include_types=["human"],
        include_names=["chat", "investigator", "reporter"],
    )


def _agent_input(state: State, work_key: str) -> dict:
    item = state.get("active_work_item") or {}
    continuation = work_key == INV_MSGS and (
        bool(item.get("id") and (item.get("attempt") or 0))
        or state.get("transition") in ("continue", "uncertain")
    )
    control = [HumanMessage(content=(
        f"Продолжи {item.get('id') or 'текущее расследование'} с сохранённого состояния. "
        "Не начинай базовую разведку заново, используй прежние Evidence и находки, "
        "выполни только оставшуюся часть acceptance."
    ))] if continuation else []
    public_messages = _agent_convo_messages(state)
    case_task = next(
        (getattr(message, "text", message.content) for message in reversed(public_messages)
         if isinstance(message, HumanMessage)),
        "",
    )
    return {
        "messages": [
            *public_messages,
            *(state.get(work_key) or []),
            *control,
        ],
        "agent_phase": "investigate",
        "continuation": continuation,
        "termination_reason": "",
        "callback_url": state.get("callback_url") or "",
        "artifacts": list(state.get("artifacts") or []),
        "findings": list(state.get("findings") or []),
        "evidence": list(state.get("evidence") or []),
        "tool_calls": list(state.get("tool_calls") or []),
        "analysis_datasets": list(state.get("analysis_datasets") or []),
        "analysis_results": list(state.get("analysis_results") or []),
        "hypotheses": list(state.get("hypotheses") or []),
        "context": state.get("context") or {},
        "last_report": state.get("last_report") or {},
        "inv_summary": state.get("inv_summary") or "",
        "inv_result": state.get("inv_result") or {},
        "scout_note": state.get("scout_note") or "",
        "scout_result": state.get("scout_result") or {},
        "active_skill": state.get("active_skill") or {},
        "investigation_scope": state.get("investigation_scope") or {},
        "investigation_plan": state.get("investigation_plan") or {},
        "work_items": list(state.get("work_items") or []),
        "work_mode": state.get("work_mode") or "",
        "depth_history": list(state.get("depth_history") or []),
        "run_metrics": list(state.get("run_metrics") or []),
        "case_task": case_task,
        "mcp_reuse_run_id": state.get("mcp_reuse_run_id") or "",
        "run_deadline_at": state.get("run_deadline_at") or "",
        "independent_verification": state.get("independent_verification") or {},
        "verification_checks": list(state.get("verification_checks") or []),
        "tenant": state.get("tenant") or "",
        "source_endpoint": state.get("source_endpoint") or "",
        "schema_version": state.get("schema_version") or "",
        "time_semantics": state.get("time_semantics") or "",
        "time_axes": state.get("time_axes") or {},
        "control_overrides": state.get("control_overrides") or {},
        "active_work_item": state.get("active_work_item") or {},
        "skill_runs": list(state.get("skill_runs") or []),
        "anchors": list(state.get("anchors") or []),
        "visited_anchors": list(state.get("visited_anchors") or []),
        "memory_loaded": state.get("memory_loaded") or "",
        "memory_error": state.get("memory_error") or "",
        "context_memories": list(state.get("context_memories") or []),
        "similar_cases": list(state.get("similar_cases") or []),
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


def _limit_reached(result: dict, agent: str) -> bool:
    counts = result.get("run_tool_call_count") or {}
    return counts.get("__all__", 0) >= TOOL_LIMITS[agent]


def _limit_note(agent: str) -> str:
    return (
        f"Исчерпан лимит {TOOL_LIMITS[agent]} вызовов инструментов. "
        "Этап завершён по уже собранным данным; непроверенные действия перечислены в ограничениях."
    )


def _fallback_limited(schema, agent: str):
    note = _limit_note(agent)
    if schema is llm_mod.ScoutResult:
        return llm_mod.ScoutResult(
            note=note,
            escalation={"needed": True, "reason": "tool_limit", "suggested_skill": None},
        )
    if schema is llm_mod.InvestigationResult:
        return llm_mod.InvestigationResult(
            status="partial",
            summary=note,
            limitations=[note],
            not_checked=[note],
        )
    return llm_mod.ReporterResult(
        answer=f"Работа остановлена по техническому ограничению. {note}",
        limitations=[note],
        not_checked=[note],
    )


async def _finalize_limited(agent: str, result: dict, schema):
    """Получить схемный результат без инструментов после ToolCallLimitMiddleware."""
    state = {**result, "context": result.get("context") or {}}
    blocks = [context_block(state), evidence_block(state), chain_block(state)]
    if result.get("inv_result"):
        blocks.append(structured_block("Структурированный результат инвестигейтора", result["inv_result"]))
    system = system_prompt(compose("base", agent), *blocks)
    instruction = HumanMessage(content=(
        _limit_note(agent)
        + " Новые инструменты вызывать нельзя. Немедленно сформируй итоговый structured response "
          "по уже имеющимся сообщениям и Evidence. Не возвращай текст middleware как ответ."
    ))
    model = llm_mod.model_for_role(agent)
    structured = model.with_structured_output(schema, method="function_calling").with_fallbacks([
        model.with_structured_output(schema, method="json_mode")
    ])
    try:
        response = await structured.ainvoke([
            SystemMessage(content=system),
            *_structured_history_messages(result.get("messages") or []),
            instruction,
        ], config=trace_config(f"{agent}_fallback", result))
    except Exception:
        response = _fallback_limited(schema, agent)
    result = response.model_dump()
    note = _limit_note(agent)
    if schema is not llm_mod.ScoutResult and note not in result.setdefault("limitations", []):
        result["limitations"].append(note)
    if schema is llm_mod.InvestigationResult:
        result["status"] = "partial"
    return result


def _timeout_result(schema, state: dict, reason: str = "hard_timeout") -> dict:
    if reason == "provider_error":
        note = "Временная ошибка LLM-провайдера: этап завершён по сохранённым данным."
    elif reason == "empty_response":
        note = "Модель вернула пустой ответ без structured response; этап завершён по сохранённым данным."
    else:
        note = "Технический таймаут: этап остановлен из-за слишком долгого ответа."
    if schema is llm_mod.ScoutResult:
        structured = llm_mod.ScoutResult(note=note)
    elif schema is llm_mod.InvestigationResult:
        structured = llm_mod.InvestigationResult(status="partial", summary=note, limitations=[note])
    else:
        structured = llm_mod.ReporterResult(answer=note, limitations=[note])
    return {
        **state,
        "messages": [*(state.get("messages") or []), AIMessage(content=note)],
        "structured_response": structured,
        "agent_phase": "fallback",
        "termination_reason": reason,
    }


def _prepare_agent_runtime(state: dict) -> dict:
    return {
        "agent_phase": state.get("agent_phase") or "investigate",
        "continuation": bool(state.get("continuation")),
        "termination_reason": state.get("termination_reason") or "",
    }


def _complete_agent_runtime(state: dict) -> dict:
    if state.get("structured_response") is not None and not state.get("termination_reason"):
        return {"termination_reason": "done"}
    return {}


def _native_agent_runtime(agent):
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(AgentWorkState)
    builder.add_node("prepare_agent", _prepare_agent_runtime)
    builder.add_node("agent", agent)
    builder.add_node("complete_agent", _complete_agent_runtime)
    builder.add_edge(START, "prepare_agent")
    builder.add_edge("prepare_agent", "agent")
    builder.add_edge("agent", "complete_agent")
    builder.add_edge("complete_agent", END)
    return builder.compile()


def _restore_stream_state(latest: dict, events: dict) -> dict:
    from subgraph import apply_delta

    result = latest
    for event_id, update in events.items():
        applied = any(
            getattr(message, "tool_call_id", None) == event_id
            for message in result.get("messages") or []
        )
        if not applied:
            result = apply_delta(result, update)
    result = dict(result)
    evidence = {}
    for item in result.get("evidence") or []:
        evidence[item.get("id")] = item
    result["evidence"] = list(evidence.values())
    for key in ("tool_calls", "analysis_datasets", "analysis_results"):
        unique = {}
        for item in result.get(key) or []:
            unique[item.get("id")] = item
        result[key] = list(unique.values())
    artifacts = {}
    for item in result.get("artifacts") or []:
        identity = (
            item.get("key"),
            item.get("filename"),
            item.get("bytes"),
            item.get("data_url"),
        )
        artifacts[identity] = item
    result["artifacts"] = list(artifacts.values())
    findings = {}
    for item in result.get("findings") or []:
        identity = (
            item.get("timestamp"),
            item.get("claim"),
            tuple(item.get("evidence_ids") or []),
            item.get("next_action"),
        )
        findings[identity] = item
    result["findings"] = list(findings.values())
    return result


def _prefer_stream_state(latest: dict, candidate: dict) -> dict:
    latest_messages = latest.get("messages") or []
    candidate_messages = candidate.get("messages") or []
    if len(candidate_messages) > len(latest_messages):
        return candidate
    if len(candidate_messages) < len(latest_messages):
        return latest
    if candidate.get("structured_response") is not None:
        return candidate
    if latest.get("structured_response") is None:
        return candidate
    return latest


def _new_evidence(before: dict, after: dict) -> list[dict]:
    previous = {item.get("id") for item in before.get("evidence") or [] if item.get("id")}
    return [item for item in after.get("evidence") or [] if item.get("id") not in previous]


def _transient_model_error(exc: Exception) -> bool:
    from openai import APIError

    if not isinstance(exc, APIError):
        return False
    status = getattr(exc, "status_code", None)
    return status is None or status == 429 or status >= 500


async def _stream_agent(agent: str, agent_input: dict, deadline: float) -> tuple[dict, bool]:
    import time

    from openai import APIError
    from langgraph.graph.state import CompiledStateGraph

    runtime = _agents[agent]
    native = isinstance(runtime, CompiledStateGraph)
    if native:
        runtime = _native_agent_runtime(runtime)
    agent_input = {
        **agent_input,
        "messages": _structured_history_messages(agent_input.get("messages") or []),
    }
    latest = agent_input
    events = {}

    async def consume():
        nonlocal latest
        config = trace_config(agent, latest)
        if native:
            stream = runtime.astream(
                agent_input,
                config=config,
                stream_mode=["values", "custom"],
                subgraphs=True,
            )
            async for _, mode, payload in stream:
                if mode == "values" and isinstance(payload, dict):
                    latest = _prefer_stream_state(latest, payload)
                elif mode == "custom" and isinstance(payload, dict) and payload.get("agent_runtime_event"):
                    events[payload["event_id"]] = payload["update"]
        elif hasattr(runtime, "astream"):
            stream = runtime.astream(agent_input, config=config, stream_mode=["values", "custom"])
            async for mode, payload in stream:
                if mode == "values" and isinstance(payload, dict):
                    latest = _prefer_stream_state(latest, payload)
                elif mode == "custom" and isinstance(payload, dict) and payload.get("agent_runtime_event"):
                    events[payload["event_id"]] = payload["update"]
        else:
            latest = await runtime.ainvoke(agent_input, config=config)

    remaining = max(0.001, deadline - time.monotonic())
    try:
        await asyncio.wait_for(consume(), timeout=remaining)
    except TimeoutError:
        return _restore_stream_state(latest, events), True
    except APIError as exc:
        if not _transient_model_error(exc):
            raise
        latest = {**latest, "termination_reason": "provider_error"}
        return _restore_stream_state(latest, events), True
    return _restore_stream_state(latest, events), False


def _finalize_instruction(agent: str, attempt: int) -> HumanMessage:
    action = "Преобразуй собранные данные в требуемый structured response." if not attempt else (
        "Предыдущий ответ не завершил structured response. Исправь его без продолжения исследования."
    )
    return HumanMessage(content=(
        f"Техническая фаза завершения роли {agent}. Новые данные не запрашивай, "
        "investigative tools недоступны. Зафиксируй выводы, ограничения и NOT_CHECKED "
        f"только по уже собранным Evidence и находкам. {action}"
    ))


async def _finalize_agent(
    agent: str,
    result: dict,
    schema,
    hard_deadline: float,
) -> tuple[dict, int]:
    import time

    calls = 0
    reason = result.get("termination_reason") or "soft_timeout"
    for attempt in range(settings.agent_finalize_attempts):
        now = time.monotonic()
        if now >= hard_deadline:
            break
        result = {
            **result,
            "messages": [*(result.get("messages") or []), _finalize_instruction(agent, attempt)],
            "run_tool_call_count": {},
            "agent_phase": "finalize",
            "termination_reason": reason,
        }
        deadline = min(hard_deadline, now + settings.agent_finalize_attempt_timeout_seconds)
        result, _ = await _stream_agent(agent, result, deadline)
        calls += (result.get("run_tool_call_count") or {}).get("__all__", 0)
        if result.get("structured_response") is not None:
            try:
                _structured_response(result, schema)
                return result, calls
            except RuntimeError:
                pass
    fallback_reason = reason if reason in ("provider_error", "run_timeout") else "finalize_exhausted"
    return _timeout_result(schema, result, fallback_reason), calls


async def _run_agent(agent: str, state: State, work_key: str) -> tuple[dict, list[BaseMessage], dict]:
    """Выполнить роль с учётом tool limit, finalization и допустимых продолжений."""
    import time

    from metrics import summarize_agent_run
    from deadline import remaining_seconds

    started_at = datetime.now(UTC)
    monotonic_start = time.monotonic()
    remaining = remaining_seconds(state)
    role_timeout = settings.agent_timeout_seconds if remaining is None else min(settings.agent_timeout_seconds, remaining)
    soft_deadline = monotonic_start + min(settings.agent_finalize_after_seconds, role_timeout)
    hard_deadline = monotonic_start + role_timeout
    original_input = _agent_input(state, work_key)
    agent_input = original_input
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
    total_calls = 0
    segments_used = 0
    finalized = False
    max_continuations = 0 if agent == "scout" else settings.agent_max_continuations
    for segment in range(max_continuations + 1):
        segments_used = segment + 1
        limited = False
        for attempt in range(2):
            if role_timeout <= 0:
                result = _timeout_result(schema, agent_input, "run_timeout")
                timed_out = True
            else:
                result, timed_out = await _stream_agent(agent, agent_input, soft_deadline)
            total_calls += (result.get("run_tool_call_count") or {}).get("__all__", 0)
            if timed_out:
                result, finalize_calls = await _finalize_agent(agent, result, schema, hard_deadline)
                total_calls += finalize_calls
                finalized = True
                break
            limited = result.get("structured_response") is None and _limit_reached(result, agent)
            if limited and segment < max_continuations:
                break
            if limited:
                result["structured_response"] = await _finalize_limited(agent, result, schema)
            try:
                _structured_response(result, schema)
                break
            except RuntimeError:
                new_messages = result["messages"][len(agent_input["messages"]):]
                if attempt == 0 and not new_messages:
                    continue
                result = _timeout_result(schema, result, "empty_response")
                break
        if finalized or not limited:
            break
        if agent == "investigator" and not _new_evidence(agent_input, result):
            result["termination_reason"] = "no_progress"
            if result.get("structured_response") is None:
                result["structured_response"] = await _finalize_limited(agent, result, schema)
            finalized = True
            break
        if segment == max_continuations:
            break
        agent_input = {
            **result,
            "run_tool_call_count": {},
            "agent_phase": "investigate",
            "continuation": True,
            "messages": [
                *(result.get("messages") or []),
                HumanMessage(content=(
                    "Продолжи незавершённую работу с текущего места. Это новый итерационный шаг "
                    "с обновлённым техническим лимитом, а не финал. Не повторяй уже выполненные "
                    "запросы и чтение Evidence. Используй накопленные handoff и заверши structured response."
                )),
            ],
            "progress": agent_progress(
                {**result, "tool_usage": {}},
                agent,
                f"Продолжаю работу, шаг {segment + 2}/{max_continuations + 1}",
                agent,
            ),
        }

    input_count = len(original_input["messages"])
    new_messages = list(result["messages"][input_count:])
    update: dict = {}
    for key in (
        "artifacts", "findings", "evidence", "tool_calls", "analysis_datasets",
        "analysis_results", "verification_checks",
    ):
        before = len(original_input[key])
        values = list(result.get(key) or [])
        if len(values) > before:
            update[key] = values[before:]
    if result.get("hypotheses") != original_input["hypotheses"]:
        update["hypotheses"] = list(result.get("hypotheses") or [])
    if result.get("independent_verification") != original_input["independent_verification"]:
        update["independent_verification"] = result.get("independent_verification") or {}
    if result.get("last_report") != original_input["last_report"]:
        update["last_report"] = result.get("last_report") or {}
    if result.get("context") != original_input["context"]:
        update["context"] = result.get("context") or original_input["context"]
    usage = dict(state.get("tool_usage") or {})
    usage[agent] = total_calls or usage.get(agent, 0)
    update["tool_usage"] = usage
    finished_at = datetime.now(UTC)
    item = state.get("active_work_item") or {}
    update["run_metrics"] = [summarize_agent_run(
        new_messages,
        agent=agent,
        model=llm_mod.model_name_for_role(agent),
        investigation_id=(state.get("context") or {}).get("id") or "",
        work_item_id=item.get("id") or "",
        attempt=(item.get("attempt") or 0) + 1 if item.get("id") else 0,
        scope_mode=(state.get("investigation_scope") or {}).get("mode") or "",
        agent_phase=result.get("agent_phase") or "investigate",
        continuation=bool(result.get("continuation")),
        termination_reason=result.get("termination_reason") or "done",
        started_at=started_at.isoformat(timespec="milliseconds"),
        finished_at=finished_at.isoformat(timespec="milliseconds"),
        duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
        segments=segments_used,
    )]
    return result, new_messages, update


def _work_mode_update(state: State, mode: str, reason: str) -> dict:
    update: dict = {"work_mode": mode}
    if state.get("work_mode") != mode:
        history = list(state.get("depth_history") or [])
        history.append({
            "from": state.get("work_mode"),
            "to": mode,
            "reason": reason,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        })
        update["depth_history"] = history
    return update


async def node_scout(state: State) -> dict:
    """Quick-проверка; при необходимости автоматически передаёт работу в deep."""
    result, new_messages, update = await _run_agent("scout", state, SCOUT_MSGS)
    structured = _structured_response(result, llm_mod.ScoutResult)
    update[SCOUT_MSGS] = new_messages[:-1]
    update["scout_result"] = structured
    update["scout_note"] = structured["note"]
    escalation = structured.get("escalation") or {}
    needs_deep = bool(escalation.get("needed"))
    update.update(_work_mode_update(state, "deep" if needs_deep else "quick", escalation.get("reason") or "quick_complete"))
    skill = get_skill(escalation.get("suggested_skill")) if needs_deep else None
    update["active_skill"] = {
        **skill.model_dump(),
        "selected_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
    } if skill else {}
    summary = structured.get("answer_candidate") or structured["note"]
    coverage = structured.get("coverage") or {}
    if coverage.get("status") and coverage["status"] != "complete":
        summary += f"\n\n- **Полнота данных:** {coverage['status']}. {coverage.get('details') or ''}".rstrip()
    if needs_deep:
        summary += f"\n\n- **Следующий этап:** глубокое расследование — {escalation.get('reason') or 'нужны дополнительные проверки'}"
    update["messages"] = [
        *_public_notifications(new_messages),
        AIMessage(content=summary, name="scout"),
    ]
    progress_state = {**state, "tool_usage": update["tool_usage"]}
    if needs_deep:
        text = {
            "incident_dating": "Ищу первое появление",
            "event_drilldown": "Разбираю событие",
            "entity_pivot": "Проверяю связанные сущности",
        }.get(skill.id if skill else None, "Углубляю анализ")
        update["progress"] = agent_progress(progress_state, "investigator", text, "investigator")
    else:
        update["progress"] = agent_progress(progress_state, "reporter", "Данных достаточно — формирую ответ", "reporter")
    return update


def node_chat(state: State) -> dict:
    """Чат-агент: простой вопрос без инструментов."""
    from documentation import documentation_block

    system = system_prompt(compose("base", "chat"), scout_block(state))
    documentation = documentation_block()
    messages = [SystemMessage(content=system), SystemMessage(content=documentation)]
    convo = with_prompt_override(state, "chat", [*messages, *_agent_convo_messages(state)])
    audit_messages("chat", state, convo)
    from deadline import run_expired

    if run_expired(state):
        response = AIMessage(content="Работа остановлена по техническому таймауту расследования.")
    else:
        response = llm_mod.llm.invoke(convo, config=trace_config("chat", state))
    update = _agent_final(state, response, "chat")
    update["progress"] = done_progress()
    return update


def _skill_run_update(state: State, result: dict, structured: dict) -> dict:
    from skill_runtime import skill_run_update

    return skill_run_update(state, result, structured)


async def node_investigator(state: State) -> dict:
    """Инвестигейтор: завершает работу резюмем для критика и репортёра."""
    result, new_messages, update = await _run_agent("investigator", state, INV_MSGS)
    structured = _structured_response(result, llm_mod.InvestigationResult)
    active_item = state.get("active_work_item") or {}
    no_progress = result.get("termination_reason") == "no_progress" or (
        bool(active_item.get("id"))
        and (active_item.get("attempt") or 0) > 0
        and not _new_evidence(state, result)
    )
    if no_progress:
        note = "Повторный проход не добавил новых Evidence; доработка остановлена без доказательства acceptance."
        result["termination_reason"] = "no_progress"
        if update.get("run_metrics"):
            update["run_metrics"][-1]["termination_reason"] = "no_progress"
        structured["status"] = "partial"
        if note not in structured.setdefault("limitations", []):
            structured["limitations"].append(note)
        unchecked = (active_item.get("critic") or {}).get("feedback") or active_item.get("acceptance") or note
        if unchecked not in structured.setdefault("not_checked", []):
            structured["not_checked"].append(unchecked)
        if structured.get("skill_run"):
            structured["skill_run"]["status"] = "partial"
            if unchecked not in structured["skill_run"].setdefault("not_checked", []):
                structured["skill_run"]["not_checked"].append(unchecked)
    if structured.get("limitations") and structured.get("status") == "done":
        structured["status"] = "partial"
    final = AIMessage(content=structured["summary"], name="investigator")
    update[INV_MSGS] = [*new_messages[:-1], final]
    update["messages"] = _public_notifications(new_messages)
    update.update(_skill_run_update(state, result, structured))
    if active_item.get("id"):
        item_context = update.pop("context", result.get("context") or state.get("context") or {})
        attempt = (active_item.get("attempt") or 0) + 1
        critic = active_item.get("critic") or {}
        if no_progress:
            critic = {
                "verdict": "rework_data",
                "feedback": note,
                "issues": [{
                    "claim": structured["summary"],
                    "evidence_ids": [],
                    "reason": note,
                    "suggested_action": "rework_data",
                }],
            }
        completed_item = {
            **active_item,
            "status": "failed" if no_progress else "critic_review" if settings.critic_enabled else "done",
            "attempt": attempt,
            "started_at": active_item.get("started_at") or datetime.now(UTC).isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "result": structured,
            "metrics": (update.get("run_metrics") or [{}])[-1],
            "critic": critic,
            "context": item_context,
        }
        update["active_work_item"] = completed_item
        update["work_items"] = [completed_item]
        update["progress"] = simple_progress(
            "planner",
            f"Часть {active_item['id']} остановлена без новых данных" if no_progress else
            f"Проверяю часть {active_item['id']}" if settings.critic_enabled else f"Часть {active_item['id']} готова",
        )
        return update
    update["inv_result"] = structured
    update["inv_summary"] = structured["summary"]
    update.update(_work_mode_update(state, "deep", "deep_investigation"))
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
    if state.get("memory_error"):
        note = state["memory_error"] + " Текущий результат сохранён только в state треда."
        if note not in structured.setdefault("limitations", []):
            structured["limitations"].append(note)
    update[REP_MSGS] = new_messages[:-1]
    update["report_result"] = structured
    previous_report = state.get("last_report") or {}
    if (
        state.get("rework", 0) > 0
        and (state.get("critic") or {}).get("verdict") == "rework_report"
        and previous_report
    ):
        from artifacts import strip_verification
        from verification import finalize_verification

        current_report = result.get("last_report") or {}
        changed = (
            int(current_report.get("revision") or 0) > int(previous_report.get("revision") or 0)
            and strip_verification(current_report.get("markdown") or "")
            != strip_verification(previous_report.get("markdown") or "")
        )
        if not changed:
            detail = "Reporter не сохранил изменённый Markdown после rework_report; прежний файл не считается исправленным."
            structured["answer"] = detail
            update["report_result"] = structured
            update["critic"] = {
                **(state.get("critic") or {}),
                "verdict": "update_failed",
                "feedback": detail,
            }
            update["suggestions"] = []
            return finalize_verification(state, update, "update_failed", detail)
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
        update["progress"] = done_progress()
    update.update(_agent_final(
        state,
        AIMessage(content=structured["answer"]),
        "reporter",
        artifacts=list(result.get("artifacts") or []),
    ))
    return update
