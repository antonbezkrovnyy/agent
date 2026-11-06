"""Тулзы вложенных агентов.

- MCP-тулзы Логикора (mcp_client).
- log_finding / list_findings / get_finding — журнал расследования.
- get_evidence — полная локальная запись по ID без повторного MCP-запроса.
- save_report / send_report — отчёты с детерминированной цепочкой расследования.

Каждый create_agent хранит tool-цикл во внутреннем канале messages.
Внешний узел переносит его в приватный канал, не открывая пользователю.
"""

import datetime
import json
from typing import Any

from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from contracts import EvidenceRecord, FindingRecord, Hypothesis, HypothesisEvent, json_dumps
from artifacts import create_report_artifacts
from evidence import evidence_record
from llm import WindowUpdate
from mcp_client import get_mcp_tools
from progress import TOOL_LIMITS, simple_progress
from reporting import post_report


def _injected_runtime_repr(_):
    return "ToolRuntime(<injected>)"


ToolRuntime.__repr__ = _injected_runtime_repr

mcp_tools = get_mcp_tools()
_MCP_TOOLS_BY_NAME = {item.name: item for item in mcp_tools}
_MCP_TOOL_NAMES = set(_MCP_TOOLS_BY_NAME)


def _is_windowed_tool(name: str) -> bool:
    tool = _MCP_TOOLS_BY_NAME[name]
    try:
        schema = tool.args or {}
        properties = schema.get("properties") or schema
    except Exception:
        properties = {}
    return "time_from" in properties or "time_to" in properties


def _result_status(data: dict) -> str:
    if data.get("ok") is False:
        return "error"
    coverage = data.get("time_coverage") or {}
    if data.get("complete") is False or coverage.get("complete") is False or data.get("truncated"):
        return "partial"
    return "ok"


def _with_progress(request, result):
    current = request.state.get("progress") or {}
    agent = current.get("agent")
    if agent not in TOOL_LIMITS:
        return result
    counts = request.state.get("run_tool_call_count") or {}
    calls = counts.get("__all__", current.get("tool_calls", 0))
    command_progress = result.update.get("progress") or {} if isinstance(result, Command) else {}
    progress = {
        **current,
        **command_progress,
        "tool_calls": min(calls, TOOL_LIMITS[agent]),
        "tool_limit": TOOL_LIMITS[agent],
    }
    if isinstance(result, Command):
        return Command(update={**result.update, "progress": progress})
    if isinstance(result, ToolMessage):
        return Command(update={"messages": [result], "progress": progress})
    return result


def build_mcp_evidence(
    name: str,
    args: dict,
    data: dict,
    evidence_id: str,
    investigation_id: str | None,
    run_id: str | None = None,
) -> dict:
    return EvidenceRecord(
        id=evidence_id,
        investigation_id=investigation_id,
        run_id=run_id,
        step_id=evidence_id,
        tool=name,
        arguments=args,
        iql=data.get("iql"),
        window=data.get("window") or {
            "from": args.get("time_from"),
            "to": args.get("time_to"),
            "source": "investigation-context",
        },
        status=_result_status(data),
        complete=_result_status(data) == "ok",
        result_ref=f"evidence://{investigation_id}/{evidence_id}",
        created_at=datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        result=data,
        error=data.get("error"),
    ).model_dump()


def _emit_tool_update(request, result):
    update = result.update if isinstance(result, Command) else {"messages": [result]}
    try:
        from langgraph.config import get_stream_writer

        get_stream_writer()({
            "agent_runtime_event": True,
            "event_id": request.tool_call["id"],
            "update": update,
        })
    except RuntimeError:
        pass


def _reusable_evidence(state: dict, name: str, args: dict) -> dict | None:
    signature = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    run_id = state.get("mcp_reuse_run_id") or None
    for evidence in reversed(state.get("evidence") or []):
        if evidence.get("run_id") != run_id or evidence.get("tool") != name or evidence.get("status") != "ok":
            continue
        candidate = json.dumps(evidence.get("arguments") or {}, ensure_ascii=False, sort_keys=True, default=str)
        if candidate == signature:
            return evidence
    return None


def _reused_mcp_result(request, evidence: dict, *, include_evidence: bool = False):
    name = request.tool_call["name"]
    data = evidence.get("result") or {}
    message = ToolMessage(
        content=json_dumps({
            "evidence_id": evidence["id"],
            "reused": True,
            **data,
        }, ensure_ascii=False),
        tool_call_id=request.tool_call["id"],
        name=name,
    )
    update = {"messages": [message]}
    if include_evidence:
        update["evidence"] = [evidence]
    result = _with_progress(request, Command(update=update))
    _emit_tool_update(request, result)
    return result


_INFLIGHT_MCP = {}


@wrap_tool_call
async def normalize_tool_result(request, handler):
    """Зафиксировать окно и Evidence; идентичные вызовы выполнять один раз."""
    name = request.tool_call["name"]
    if name not in _MCP_TOOL_NAMES:
        result = _with_progress(request, await handler(request))
        _emit_tool_update(request, result)
        return result

    args = dict(request.tool_call.get("args") or {})
    context = request.state.get("context") or {}
    focus = context.get("focus_window") or {}
    default_from = focus.get("time_from") or context.get("time_from")
    default_to = focus.get("time_to") or context.get("time_to")
    if _is_windowed_tool(name) or "time_from" in args or "time_to" in args:
        if not args.get("time_from") and default_from:
            args["time_from"] = default_from
        if not args.get("time_to") and default_to:
            args["time_to"] = default_to
        request = request.override(tool_call={**request.tool_call, "args": args})

    reusable = _reusable_evidence(request.state, name, args)
    if reusable is not None:
        return _reused_mcp_result(request, reusable)

    import asyncio

    investigation_id = context.get("id")
    run_id = request.state.get("mcp_reuse_run_id") or None
    flight_key = json.dumps(
        [investigation_id, run_id, name, args], ensure_ascii=False, sort_keys=True, default=str,
    ) if investigation_id else None
    if flight_key and (pending := _INFLIGHT_MCP.get(flight_key)) is not None:
        evidence = await asyncio.shield(pending)
        return _reused_mcp_result(request, evidence, include_evidence=True)

    future = asyncio.get_running_loop().create_future() if flight_key else None
    if flight_key:
        _INFLIGHT_MCP[flight_key] = future
    try:
        try:
            result = await handler(request)
            text = result.text or json_dumps(result.content, ensure_ascii=False)
            try:
                data = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                data = {"text": text}
            if not isinstance(data, dict):
                data = {"result": data}
            message = result.model_copy(update={"content": text})
        except Exception as exc:
            data = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            text = json_dumps(data, ensure_ascii=False)
            message = ToolMessage(
                content=text,
                tool_call_id=request.tool_call["id"],
                name=name,
                status="error",
            )

        evidence_id = request.tool_call["id"]
        evidence = build_mcp_evidence(name, args, data, evidence_id, investigation_id, run_id)
        if future is not None and not future.done():
            future.set_result(evidence)
        message = message.model_copy(update={
            "content": json_dumps({"evidence_id": evidence_id, **data}, ensure_ascii=False)
        })
        result = _with_progress(request, Command(update={"messages": [message], "evidence": [evidence]}))
        _emit_tool_update(request, result)
        return result
    except BaseException as exc:
        if future is not None and not future.done():
            future.set_exception(exc)
        raise
    finally:
        if flight_key and _INFLIGHT_MCP.get(flight_key) is future:
            _INFLIGHT_MCP.pop(flight_key, None)


@tool
def get_evidence(evidence_id: str, runtime: ToolRuntime = None) -> str:
    """Прочитать полную запись Evidence по ID без нового запроса к LogiCore."""
    state = runtime.state if runtime else {}
    record = evidence_record(state, evidence_id)
    if record is None:
        available = [item.get("id") for item in state.get("evidence") or []]
        return json_dumps({
            "ok": False,
            "error": "Evidence не найдено",
            "evidence_id": evidence_id,
            "available": available,
        }, ensure_ascii=False)
    return json_dumps({"ok": True, "evidence": record}, ensure_ascii=False)


@tool
def get_evidence_batch(evidence_ids: list[str], runtime: ToolRuntime = None) -> str:
    """Прочитать несколько полных Evidence одним вызовом без запросов к LogiCore."""
    state = runtime.state if runtime else {}
    wanted = list(dict.fromkeys(evidence_ids))[:50]
    records = state.get("evidence") or []
    by_id = {item.get("id"): item for item in records}
    return json_dumps({
        "ok": True,
        "items": [by_id[item] for item in wanted if item in by_id],
        "missing_ids": [item for item in wanted if item not in by_id],
        "truncated": len(evidence_ids) > len(wanted),
    }, ensure_ascii=False)


def _utc_iso(value: datetime.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC).isoformat()


@tool(args_schema=WindowUpdate)
def update_window(
    action: str,
    reason: str,
    time_from: datetime.datetime | None = None,
    time_to: datetime.datetime | None = None,
    runtime: ToolRuntime = None,
) -> Command:
    """Сместить, расширить или временно сузить окно текущего расследования."""
    state = runtime.state if runtime else {}
    context = dict(state.get("context") or {})
    call_id = runtime.tool_call_id if runtime else ""
    if not context.get("id"):
        message = ToolMessage(
            content=json_dumps({"ok": False, "error": "Контекст расследования отсутствует"}, ensure_ascii=False),
            tool_call_id=call_id,
            name="update_window",
            status="error",
        )
        return Command(update={"messages": [message]})

    current = {"time_from": context["time_from"], "time_to": context["time_to"]}
    if action != "preserve" and (time_from is None or time_to is None):
        message = ToolMessage(
            content=json_dumps({"ok": False, "error": "Для смены окна нужны time_from и time_to"}, ensure_ascii=False),
            tool_call_id=call_id,
            name="update_window",
            status="error",
        )
        return Command(update={"messages": [message]})

    updated = dict(context)
    active = current
    base = dict(context.get("base_window") or current)
    focus = context.get("focus_window")
    if action != "preserve":
        start = _utc_iso(time_from)
        end = _utc_iso(time_to)
        if start >= end:
            message = ToolMessage(
                content=json_dumps({"ok": False, "error": "time_from должен быть раньше time_to"}, ensure_ascii=False),
                tool_call_id=call_id,
                name="update_window",
                status="error",
            )
            return Command(update={"messages": [message]})
        requested = {"time_from": start, "time_to": end}
        if action == "expand":
            active = {
                "time_from": min(base["time_from"], start),
                "time_to": max(base["time_to"], end),
            }
            base = dict(active)
            focus = None
        elif action == "replace":
            active = requested
            base = dict(requested)
            focus = None
        else:
            active = requested
            focus = dict(requested)
        history = list(context.get("window_history") or [])
        history.append({
            "action": action,
            "from": current,
            "to": active,
            "reason": reason,
            "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        })
        updated.update({
            **active,
            "base_window": base,
            "focus_window": focus,
            "window_history": history,
        })

    payload = {
        "ok": True,
        "action": action,
        "window": active,
        "base_window": updated.get("base_window", base),
        "focus_window": updated.get("focus_window", focus),
        "reason": reason,
    }
    progress = simple_progress("investigator", f"Окно: {reason}" if action != "preserve" else "Продолжаю анализ")
    notification = AIMessage(
        content=(
            f"Окно: {current['time_from']} — {current['time_to']} → "
            f"{active['time_from']} — {active['time_to']}. Причина: {reason}"
        ),
        name="window",
    )
    return Command(update={
        "context": updated,
        "messages": [
            ToolMessage(
                content=json_dumps(payload, ensure_ascii=False),
                tool_call_id=call_id,
                name="update_window",
            ),
            notification,
        ],
        "progress": progress,
    })


@tool
def log_finding(
    claim: str,
    evidence_ids: list[str],
    next_action: str,
    runtime: ToolRuntime = None,
) -> Command:
    """Зафиксировать утверждение, evidence_id источников и следующий шаг."""
    state = runtime.state if runtime else {}
    available = {item.get("id") for item in state.get("evidence") or []}
    unknown = [item for item in evidence_ids if item not in available]
    if unknown:
        return Command(update={
            "messages": [ToolMessage(
                content=json_dumps({
                    "ok": False,
                    "error": "Неизвестные evidence_ids",
                    "unknown": unknown,
                    "available": sorted(available),
                }, ensure_ascii=False),
                tool_call_id=runtime.tool_call_id if runtime else "",
                name="log_finding",
                status="error",
            )],
        })

    note = FindingRecord(
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        claim=claim,
        evidence_ids=evidence_ids,
        next_action=next_action,
    ).model_dump()
    evidence_lines = "\n".join(f"  - `{item}`" for item in evidence_ids) or "  - нет"
    notification = AIMessage(
        content=(
            f"**Находка:** {claim}\n"
            f"- **Evidence:**\n{evidence_lines}\n"
            f"- **Следующий шаг:** {next_action or 'уточнение не требуется'}"
        ),
        name="finding",
    )
    return Command(update={
        "findings": [note],
        "messages": [
            ToolMessage(
                content=json_dumps(note, ensure_ascii=False),
                tool_call_id=runtime.tool_call_id if runtime else "",
                name="log_finding",
            ),
            notification,
        ],
    })


@tool
def update_hypothesis(
    text: str,
    status: str,
    reason: str,
    evidence_ids: list[str] | None = None,
    hypothesis_id: str = "",
    runtime: ToolRuntime = None,
) -> Command:
    """Создать или пересмотреть гипотезу: open, supported или rejected."""
    state = runtime.state if runtime else {}
    call_id = runtime.tool_call_id if runtime else ""
    evidence_ids = list(dict.fromkeys(evidence_ids or []))
    available = {item.get("id") for item in state.get("evidence") or []}
    unknown = [item for item in evidence_ids if item not in available]
    error = ""
    if unknown:
        error = f"Неизвестные evidence_ids: {unknown}"
    elif status not in ("open", "supported", "rejected"):
        error = "status должен быть open, supported или rejected"
    elif status != "open" and not evidence_ids:
        error = "supported/rejected требуют хотя бы один evidence_id"
    existing = next(
        (item for item in state.get("hypotheses") or [] if item.get("id") == hypothesis_id),
        None,
    )
    if hypothesis_id and existing is None:
        error = "Гипотеза с указанным hypothesis_id не найдена"
    if error:
        return Command(update={"messages": [ToolMessage(
            content=json_dumps({"ok": False, "error": error}, ensure_ascii=False),
            tool_call_id=call_id,
            name="update_hypothesis",
            status="error",
        )]})

    event = HypothesisEvent(status=status, reason=reason, evidence_ids=evidence_ids)
    previous = Hypothesis.model_validate(existing) if existing else None
    record = Hypothesis(
        id=previous.id if previous else (hypothesis_id or f"hyp-{call_id}"),
        text=text,
        status=status,
        evidence_ids=evidence_ids,
        reason=reason,
        created_at=previous.created_at if previous else datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        updated_at=datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        history=[*(previous.history if previous else []), event],
    )
    notification = AIMessage(
        content=(
            f"Гипотеза: {text}\n"
            f"Статус: {status}. Причина: {reason or 'не указана'}.\n"
            f"Evidence: {', '.join(evidence_ids) or 'не требуются для open'}"
        ),
        name="hypothesis",
    )
    return Command(update={
        "hypotheses": [record.model_dump()],
        "messages": [
            ToolMessage(
                content=json_dumps({"ok": True, "hypothesis": record.model_dump()}, ensure_ascii=False),
                tool_call_id=call_id,
                name="update_hypothesis",
            ),
            notification,
        ],
    })


@tool
def save_report(
    title: str,
    markdown: str,
    filename: str | None = None,
    runtime: ToolRuntime = None,
) -> Command:
    """Сохранить отчёт файлом, который пользователь скачает из чата.

    Когда вызывать: просят «отчёт», «сохрани/выгрузи в файл», «скачай».
    Файл прикрепится к следующему за вызовом ответу автоматически.
    """
    state = runtime.state if runtime else {}
    last_report, artifacts = create_report_artifacts(title, markdown, state, filename)
    last_report["revision"] = int((state.get("last_report") or {}).get("revision") or 0) + 1
    result = {
        "ok": True,
        "filename": last_report["filename"],
        "bytes": last_report["bytes"],
        **({
            "package_filename": last_report["package_filename"],
            "package_bytes": last_report["package_bytes"],
        } if last_report.get("package_filename") else {}),
    }
    return Command(update={
        "messages": [ToolMessage(
            content=json_dumps(result, ensure_ascii=False),
            tool_call_id=runtime.tool_call_id if runtime else "",
            name="save_report",
        )],
        "artifacts": artifacts,
        "last_report": last_report,
    })


@tool
async def send_report(
    title: str,
    markdown: str,
    callback_url: str | None = None,
    runtime: ToolRuntime = None,
) -> Command:
    """Доставить готовый отчёт расследования на принимающий эндпоинт (callback).

    Когда вызывать: задача — расследование аномалии и анализ завершён.
    При успешной доставке отчёт прикрепляется файлом к ответу;
    при сбое — вместо этого вызови save_report.
    """
    state = runtime.state if runtime else {}
    last_report, artifacts = create_report_artifacts(title, markdown, state)
    last_report["revision"] = int((state.get("last_report") or {}).get("revision") or 0) + 1
    fname = last_report["filename"]
    markdown = last_report["markdown"]
    target = (callback_url or "").strip() or str(state.get("callback_url") or "").strip()
    result: dict[str, Any] = {"filename": fname, "bytes": last_report["bytes"]}
    if not target:
        result["ok"] = False
        result["error"] = "callback_url не задан: эндпоинт отсутствует в данных запуска"
    else:
        thread_id = None
        config = runtime.config if runtime else {}
        if isinstance(config, dict):
            thread_id = (config.get("configurable") or {}).get("thread_id")

        from artifact_store import artifact_url

        payload = {
            "event": "investigation_report",
            "title": title,
            "filename": fname,
            "markdown": markdown,
            "sent_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "thread_id": thread_id,
            "artifacts": [{**item, "url": artifact_url(item)} for item in artifacts],
        }
        try:
            resp = await post_report(target, payload)
            result["status"] = resp.status_code
            result["ok"] = 200 <= resp.status_code < 300
            if not result["ok"]:
                result["error"] = f"эндпоинт вернул {resp.status_code}: {resp.text[:300]}"
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"POST не удался: {type(exc).__name__}: {exc}"

    update: dict = {
        "messages": [ToolMessage(
            content=json_dumps(result, ensure_ascii=False),
            tool_call_id=runtime.tool_call_id if runtime else "",
            name="send_report",
        )],
    }
    if result["ok"]:
        update["artifacts"] = artifacts
        update["last_report"] = last_report

    return Command(update=update)


@tool
def list_findings(runtime: ToolRuntime = None) -> str:
    """Весь список записей, сделанных через log_finding (с номерами).

    Когда вызывать: нужно вспомнить, что уже найдено — правки отчёта,
    продолжение расследования, вопрос «что ты нашёл».
    """
    state = runtime.state if runtime else {}
    findings = state.get("findings") or []
    if not findings:
        return "Записей нет: log_finding ещё не вызывался."
    return json_dumps(list(enumerate(findings, start=1)), ensure_ascii=False)


@tool
def get_finding(index: int, runtime: ToolRuntime = None) -> str:
    """Конкретная запись из log_finding по номеру (номера — в выводе list_findings)."""
    state = runtime.state if runtime else {}
    findings = state.get("findings") or []
    if not 1 <= index <= len(findings):
        return f"Записи №{index} нет: всего записей {len(findings)}."
    return json_dumps(findings[index - 1], ensure_ascii=False)


@tool
def list_hypotheses(runtime: ToolRuntime = None) -> str:
    """Показать компактный журнал гипотез текущего расследования."""
    state = runtime.state if runtime else {}
    items = [{
        "id": item.get("id"),
        "text": item.get("text"),
        "status": item.get("status"),
        "reason": item.get("reason"),
        "evidence_ids": item.get("evidence_ids") or [],
    } for item in state.get("hypotheses") or []]
    return json_dumps({"ok": True, "hypotheses": items}, ensure_ascii=False)


@tool
def get_hypothesis(hypothesis_id: str, runtime: ToolRuntime = None) -> str:
    """Прочитать гипотезу и полную историю её изменений по ID."""
    state = runtime.state if runtime else {}
    hypotheses = state.get("hypotheses") or []
    record = next((item for item in hypotheses if item.get("id") == hypothesis_id), None)
    if record is None:
        return json_dumps({
            "ok": False,
            "error": "Гипотеза не найдена",
            "available": [item.get("id") for item in hypotheses],
        }, ensure_ascii=False)
    return json_dumps({"ok": True, "hypothesis": record}, ensure_ascii=False)


@tool
def inspect_memory(query: str = "", limit: int = 5, runtime: ToolRuntime = None) -> str:
    """Показать read-only обзор памяти: текущее дело, факты и последние случаи.

    Не изменяет память и не запускает расследование. query задаёт тематический поиск;
    пустая строка возвращает последние активные факты и дела.
    """
    from memory import memory_overview

    state = runtime.state if runtime else {}
    config = runtime.config if runtime else {}
    return json_dumps(
        memory_overview(state, config, query, max(1, min(limit, 10))),
        ensure_ascii=False,
        default=str,
    )


@tool
def search_context_memory(query: str, limit: int = 5) -> str:
    """Найти знания окружения по новым сущностям, симптомам или гипотезе.

    Это не Evidence текущего дела. Влияющие на вывод факты перепроверяй через LogiCore.
    """
    from memory import memory, settings

    try:
        facts = memory.search_facts(
            settings.agent_memory_scope,
            query,
            max(1, min(limit, 10)),
        )
        return json_dumps({"ok": True, "facts": facts}, ensure_ascii=False)
    except Exception as exc:
        return json_dumps({
            "ok": False,
            "error": f"Context Memory недоступна: {type(exc).__name__}",
        }, ensure_ascii=False)


@tool
def search_similar_cases(query: str, limit: int = 3, runtime: ToolRuntime = None) -> str:
    """Найти компактные карточки похожих прошлых дел по сущностям и симптомам.

    Это не continuation и не Evidence текущего расследования.
    """
    from memory import memory

    state = runtime.state if runtime else {}
    config = runtime.config if runtime else {}
    configurable = (config.get("configurable") or {}) if isinstance(config, dict) else {}
    user_id = str(configurable.get("user_id") or "anonymous")
    try:
        cases = memory.search_cases(
            user_id,
            query,
            max(1, min(limit, 10)),
            (state.get("context") or {}).get("id") or "",
        )
        return json_dumps({"ok": True, "cases": cases}, ensure_ascii=False, default=str)
    except Exception as exc:
        return json_dumps({
            "ok": False,
            "error": f"Поиск похожих дел недоступен: {type(exc).__name__}",
        }, ensure_ascii=False)


# ── Тулзы на агента ──────────────────────────────────────────────
SCOUT_MCP_TOOL_NAMES = {
    "server_time",
    "count_events",
    "timeline",
    "top_values",
    "top_values_batch",
    "field_fill_rates",
    "duplicates",
    "field_lookup",
    "enum_lookup",
    "search_events",
    "get_event",
}
# Скаут — статистическая разведка; события только для точечных примеров.
scout_tools = [item for item in mcp_tools if item.name in SCOUT_MCP_TOOL_NAMES] + [
    log_finding,
    search_context_memory,
    search_similar_cases,
]
# Инвестигейтор — MCP + журнал расследования
investigator_tools = list(mcp_tools or []) + [
    log_finding,
    list_findings,
    get_finding,
    get_evidence,
    get_evidence_batch,
    update_window,
    list_hypotheses,
    get_hypothesis,
    update_hypothesis,
    inspect_memory,
    search_context_memory,
    search_similar_cases,
]
# Репортёр — отчёты, чтение журнала, Evidence и детерминированная выгрузка
extract_data_tool = next((item for item in mcp_tools if item.name == "extract_data"), None)
reporter_tools = [save_report, send_report, list_findings, get_finding, get_evidence_batch, inspect_memory]
if extract_data_tool is not None:
    reporter_tools.append(extract_data_tool)
# Чат-агент — без тулз
chat_tools: list = []
