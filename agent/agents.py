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
from config import Settings
from evidence import chain_block
from prompts import (
    compose,
    context_block,
    critic_feedback_block,
    evidence_block,
    inv_summary_block,
    last_report_block,
    memory_block,
    scout_block,
    skill_block,
    structured_block,
    system_prompt,
)
from progress import TOOL_LIMITS, agent_progress, done_progress, report_suggestions
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
        ToolCallLimitMiddleware(run_limit=TOOL_LIMITS[role], exit_behavior="end"),
    ]


@dynamic_prompt
def _scout_prompt(request: ModelRequest) -> str:
    return system_prompt(compose("base", "scout"), context_block(request.state))


@dynamic_prompt
def _investigator_prompt(request: ModelRequest) -> str:
    state = request.state
    blocks = [
        context_block(state),
        scout_block(state),
        skill_block(state),
        evidence_block(state),
        structured_block("Журнал гипотез", {"items": state.get("hypotheses") or []}),
        memory_block(state),
        critic_feedback_block(state),
    ]
    return system_prompt(compose("base", "investigator"), *blocks)


@dynamic_prompt
def _reporter_prompt(request: ModelRequest) -> str:
    state = request.state
    blocks: list[str] = [context_block(state), scout_block(state), evidence_block(state), chain_block(state)]
    if state.get("inv_result"):
        blocks.append(structured_block("Структурированный результат инвестигейтора", state["inv_result"]))
    elif state.get("inv_summary"):
        blocks.append(inv_summary_block(state["inv_summary"]))
    if state.get("last_report"):
        blocks.append(last_report_block(state["last_report"]))
    blocks.append(memory_block(state))
    blocks.append(critic_feedback_block(state))
    return system_prompt(compose("base", "reporter"), *blocks)


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
        "hypotheses": list(state.get("hypotheses") or []),
        "context": state.get("context") or {},
        "last_report": state.get("last_report") or {},
        "inv_summary": state.get("inv_summary") or "",
        "inv_result": state.get("inv_result") or {},
        "scout_note": state.get("scout_note") or "",
        "scout_result": state.get("scout_result") or {},
        "active_skill": state.get("active_skill") or {},
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
    if result.get("hypotheses") != agent_input["hypotheses"]:
        update["hypotheses"] = list(result.get("hypotheses") or [])
    if result.get("last_report") != agent_input["last_report"]:
        update["last_report"] = result.get("last_report") or {}
    if result.get("context") != agent_input["context"]:
        update["context"] = result.get("context") or agent_input["context"]
    counts = result.get("run_tool_call_count") or {}
    usage = dict(state.get("tool_usage") or {})
    usage[agent] = counts.get("__all__", usage.get(agent, 0))
    update["tool_usage"] = usage
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
    convo = [SystemMessage(content=system), *_agent_convo_messages(state)]
    response = llm_mod.llm.invoke(convo)
    update = _agent_final(state, response, "chat")
    update["progress"] = done_progress()
    return update


def _anchor_key(anchor: dict) -> str:
    return f"{anchor.get('type', 'custom')}:{str(anchor.get('value') or '').strip().lower()}"


def _skill_run_update(state: State, result: dict, structured: dict) -> dict:
    active = state.get("active_skill") or {}
    if not active.get("id"):
        return {}
    run = structured.get("skill_run")
    if not run or run.get("skill_id") != active["id"] or run.get("version") != active["version"]:
        note = "Skill не вернул корректный structured skill_run."
        structured.setdefault("limitations", []).append(note)
        run = {
            "skill_id": active["id"],
            "version": active["version"],
            "status": "failed",
            "summary": structured.get("summary") or note,
            "window_updates": 0,
            "evidence_ids": [],
            "not_checked": [note],
        }
    available = {item.get("id") for item in result.get("evidence") or state.get("evidence") or []}
    unknown = [item for item in run.get("evidence_ids") or [] if item not in available]
    if unknown:
        run["evidence_ids"] = [item for item in run.get("evidence_ids") or [] if item in available]
        run.setdefault("not_checked", []).append("Из skill_run удалены неизвестные evidence_id.")
        run["status"] = "partial"
    before = len((state.get("context") or {}).get("window_history") or [])
    after = len((result.get("context") or {}).get("window_history") or [])
    run["window_updates"] = max(0, after - before)
    if run["window_updates"] > active.get("max_window_updates", 0):
        run.setdefault("not_checked", []).append("Превышен лимит изменений окна skill.")
        run["status"] = "partial"

    existing_anchors = list(state.get("anchors") or [])
    merged = {_anchor_key(item): dict(item) for item in existing_anchors}
    valid_anchors = []
    for anchor in run.get("anchors") or []:
        evidence_ids = [item for item in anchor.get("evidence_ids") or [] if item in available]
        if not evidence_ids:
            run.setdefault("not_checked", []).append(f"Anchor без Evidence пропущен: {_anchor_key(anchor)}.")
            run["status"] = "partial"
            continue
        anchor = {**anchor, "evidence_ids": evidence_ids}
        key = _anchor_key(anchor)
        valid_anchors.append(anchor)
        previous = merged.get(key)
        if previous:
            anchor["evidence_ids"] = list(dict.fromkeys([
                *(previous.get("evidence_ids") or []),
                *evidence_ids,
            ]))
        merged[key] = anchor
    run["anchors"] = valid_anchors
    if len(valid_anchors) > active.get("max_branches", 1):
        run.setdefault("not_checked", []).append("Превышен лимит новых anchors skill.")
        run["status"] = "partial"
    if run.get("depth", 0) > active.get("max_depth", 0):
        run.setdefault("not_checked", []).append("Превышена глубина pivot skill.")
        run["status"] = "partial"

    previous_visited = {item.strip().lower() for item in state.get("visited_anchors") or []}
    run_visited = list(dict.fromkeys(
        item.strip().lower() for item in run.get("visited_anchors") or [] if item.strip()
    ))
    if previous_visited.intersection(run_visited):
        run.setdefault("not_checked", []).append("Обнаружено повторное посещение anchors.")
        run["status"] = "partial"
    run["visited_anchors"] = run_visited
    if active["id"] == "entity_pivot":
        visited = previous_visited.union(run_visited)
        for key in sorted(set(merged).difference(visited)):
            note = f"Ветка не исследована в этом skill_run: {key}."
            if note not in run.setdefault("not_checked", []):
                run["not_checked"].append(note)
    recorded_at = datetime.now(UTC)
    run["model"] = llm_mod.model_name_for_role("investigator")
    if active.get("selected_at"):
        selected_at = datetime.fromisoformat(active["selected_at"].replace("Z", "+00:00"))
        run["duration_ms"] = max(0, int((recorded_at - selected_at).total_seconds() * 1000))
    run["recorded_at"] = recorded_at.isoformat(timespec="seconds")
    return {
        "skill_runs": [run],
        "anchors": list(merged.values()),
        "visited_anchors": sorted(previous_visited.union(run_visited)),
    }


async def node_investigator(state: State) -> dict:
    """Инвестигейтор: завершает работу резюмем для критика и репортёра."""
    result, new_messages, update = await _run_agent("investigator", state, INV_MSGS)
    structured = _structured_response(result, llm_mod.InvestigationResult)
    if structured.get("limitations") and structured.get("status") == "done":
        structured["status"] = "partial"
    final = AIMessage(content=structured["summary"], name="investigator")
    update[INV_MSGS] = [*new_messages[:-1], final]
    update.update(_skill_run_update(state, result, structured))
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
