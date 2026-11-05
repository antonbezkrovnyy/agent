"""Runtime вложенных create_agent-агентов и их узлы для внешнего workflow.

Tool-цикл каждого агента выполняется внутри create_agent. Наружу узлы
переносят только новые сообщения в приватный канал и дельты состояния;
в общий диалог попадают исключительно финальные ответы.
"""

import asyncio
from datetime import UTC, datetime

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, SummarizationMiddleware, ToolCallLimitMiddleware, dynamic_prompt
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    filter_messages,
)

import llm as llm_mod
from artifact_store import artifact_url
from config import Settings
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


def _middleware(role: str, prompt):
    return [
        SummarizationMiddleware(
            model=llm_mod.model_for_role(role),
            trigger=("tokens", settings.summarization_trigger_tokens),
            keep=("messages", settings.summarization_keep_messages),
        ),
        prompt,
        normalize_tool_result,
        ToolStepMiddleware(role),
        ToolCallLimitMiddleware(run_limit=TOOL_LIMITS[role], exit_behavior="end"),
    ]


def _audited_prompt(role: str, request: ModelRequest, prompt: str) -> str:
    prompt = effective_prompt(request.state, role, prompt)
    audit_messages(role, request.state, [SystemMessage(content=prompt), *request.messages], request.tools)
    return prompt


@dynamic_prompt
def _scout_prompt(request: ModelRequest) -> str:
    prompt = system_prompt(compose("base", "scout"), context_block(request.state))
    return _audited_prompt("scout", request, prompt)


@dynamic_prompt
def _investigator_prompt(request: ModelRequest) -> str:
    state = request.state
    blocks = [
        scope_block(state),
        work_item_block(state),
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
        "hypotheses": list(state.get("hypotheses") or []),
        "context": state.get("context") or {},
        "last_report": state.get("last_report") or {},
        "inv_summary": state.get("inv_summary") or "",
        "inv_result": state.get("inv_result") or {},
        "scout_note": state.get("scout_note") or "",
        "scout_result": state.get("scout_result") or {},
        "active_skill": state.get("active_skill") or {},
        "investigation_scope": state.get("investigation_scope") or {},
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
            *(result.get("messages") or []),
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
    """Выполнить роль с учётом tool limit и допустимых для неё продолжений."""
    from metrics import summarize_agent_run

    started_at = datetime.now(UTC)
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
    max_continuations = 0 if agent == "scout" else settings.agent_max_continuations
    for segment in range(max_continuations + 1):
        segments_used = segment + 1
        limited = False
        for attempt in range(2):
            try:
                result = await asyncio.wait_for(
                    _agents[agent].ainvoke(agent_input, config=trace_config(agent, agent_input)),
                    timeout=settings.agent_timeout_seconds,
                )
            except TimeoutError:
                result = _timeout_result(schema, agent_input["messages"])
            total_calls += (result.get("run_tool_call_count") or {}).get("__all__", 0)
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
                if attempt == 1 or new_messages:
                    raise
        if not limited or segment == max_continuations:
            break
        agent_input = {
            **result,
            "run_tool_call_count": {},
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
    for key in ("artifacts", "findings", "evidence"):
        before = len(original_input[key])
        values = list(result.get(key) or [])
        if len(values) > before:
            update[key] = values[before:]
    if result.get("hypotheses") != original_input["hypotheses"]:
        update["hypotheses"] = list(result.get("hypotheses") or [])
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
        summary += f"\n\nПолнота данных: {coverage['status']}. {coverage.get('details') or ''}".rstrip()
    if needs_deep:
        summary += f"\n\nПерехожу к глубокому расследованию: {escalation.get('reason') or 'нужны дополнительные проверки'}"
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
    system = system_prompt(compose("base", "chat"), scout_block(state))
    convo = with_prompt_override(state, "chat", [SystemMessage(content=system), *_agent_convo_messages(state)])
    audit_messages("chat", state, convo)
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
    if structured.get("limitations") and structured.get("status") == "done":
        structured["status"] = "partial"
    final = AIMessage(content=structured["summary"], name="investigator")
    update[INV_MSGS] = [*new_messages[:-1], final]
    update["messages"] = _public_notifications(new_messages)
    update.update(_skill_run_update(state, result, structured))
    active_item = state.get("active_work_item") or {}
    if active_item.get("id"):
        item_context = update.pop("context", result.get("context") or state.get("context") or {})
        attempt = (active_item.get("attempt") or 0) + 1
        update["active_work_item"] = {**active_item, "attempt": attempt}
        update["work_items"] = [{
            "id": active_item["id"],
            "status": "critic_review" if settings.critic_enabled else "done",
            "attempt": attempt,
            "started_at": active_item.get("started_at") or datetime.now(UTC).isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "result": structured,
            "metrics": (update.get("run_metrics") or [{}])[-1],
            "context": item_context,
        }]
        update["progress"] = simple_progress(
            "planner",
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
