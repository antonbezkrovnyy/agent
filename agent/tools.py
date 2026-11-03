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
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from contracts import EvidenceRecord, FindingRecord, Hypothesis, HypothesisEvent
from evidence import evidence_record, with_investigation_chain
from llm import WindowUpdate
from mcp_client import get_mcp_tools
from progress import TOOL_LIMITS, simple_progress
from reporting import build_report, post_report

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


@wrap_tool_call
async def normalize_tool_result(request, handler):
    """Зафиксировать окно, нормализовать content и сохранить Evidence."""
    name = request.tool_call["name"]
    if name not in _MCP_TOOL_NAMES:
        return _with_progress(request, await handler(request))

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

    try:
        result = await handler(request)
        text = result.text or json.dumps(result.content, ensure_ascii=False)
        try:
            data = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            data = {"text": text}
        if not isinstance(data, dict):
            data = {"result": data}
        message = result.model_copy(update={"content": text})
    except Exception as exc:
        data = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        text = json.dumps(data, ensure_ascii=False)
        message = ToolMessage(
            content=text,
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    evidence_id = request.tool_call["id"]
    investigation_id = (request.state.get("context") or {}).get("id")
    evidence = EvidenceRecord(
        id=evidence_id,
        investigation_id=investigation_id,
        step_id=evidence_id,
        tool=name,
        arguments=args,
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
    message = message.model_copy(update={
        "content": json.dumps({"evidence_id": evidence_id, **data}, ensure_ascii=False)
    })
    return _with_progress(request, Command(update={"messages": [message], "evidence": [evidence]}))


@tool
def get_evidence(evidence_id: str, runtime: ToolRuntime = None) -> str:
    """Прочитать полную запись Evidence по ID без нового запроса к LogiCore."""
    state = runtime.state if runtime else {}
    record = evidence_record(state, evidence_id)
    if record is None:
        available = [item.get("id") for item in state.get("evidence") or []]
        return json.dumps({
            "ok": False,
            "error": "Evidence не найдено",
            "evidence_id": evidence_id,
            "available": available,
        }, ensure_ascii=False)
    return json.dumps({"ok": True, "evidence": record}, ensure_ascii=False)


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
            content=json.dumps({"ok": False, "error": "Контекст расследования отсутствует"}, ensure_ascii=False),
            tool_call_id=call_id,
            name="update_window",
            status="error",
        )
        return Command(update={"messages": [message]})

    current = {"time_from": context["time_from"], "time_to": context["time_to"]}
    if action != "preserve" and (time_from is None or time_to is None):
        message = ToolMessage(
            content=json.dumps({"ok": False, "error": "Для смены окна нужны time_from и time_to"}, ensure_ascii=False),
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
                content=json.dumps({"ok": False, "error": "time_from должен быть раньше time_to"}, ensure_ascii=False),
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
    return Command(update={
        "context": updated,
        "messages": [ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=call_id,
            name="update_window",
        )],
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
                content=json.dumps({
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
    return Command(update={
        "findings": [note],
        "messages": [ToolMessage(
            content=json.dumps(note, ensure_ascii=False),
            tool_call_id=runtime.tool_call_id if runtime else "",
            name="log_finding",
        )],
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
            content=json.dumps({"ok": False, "error": error}, ensure_ascii=False),
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
    return Command(update={
        "hypotheses": [record.model_dump()],
        "messages": [ToolMessage(
            content=json.dumps({"ok": True, "hypothesis": record.model_dump()}, ensure_ascii=False),
            tool_call_id=call_id,
            name="update_hypothesis",
        )],
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
    markdown = with_investigation_chain(markdown, state)
    fname, url, nbytes = build_report(title, markdown, filename)
    payload = json.dumps({"ok": True, "filename": fname, "bytes": nbytes}, ensure_ascii=False)
    return Command(update={
        "messages": [ToolMessage(
            content=payload,
            tool_call_id=runtime.tool_call_id if runtime else "",
            name="save_report",
        )],
        "artifacts": [url],
        # Сырой текст — для будущих правок
        "last_report": {"title": title, "markdown": markdown, "filename": fname},
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
    markdown = with_investigation_chain(markdown, state)
    fname, url, nbytes = build_report(title, markdown)
    target = (callback_url or "").strip() or str(state.get("callback_url") or "").strip()

    result: dict[str, Any] = {"filename": fname, "bytes": nbytes}

    if not target:
        result["ok"] = False
        result["error"] = "callback_url не задан: эндпоинт отсутствует в данных запуска"
    else:
        thread_id = None
        config = runtime.config if runtime else {}
        if isinstance(config, dict):
            thread_id = (config.get("configurable") or {}).get("thread_id")

        payload = {
            "event": "investigation_report",
            "title": title,
            "filename": fname,
            "markdown": markdown,
            "sent_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "thread_id": thread_id,
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
            content=json.dumps(result, ensure_ascii=False),
            tool_call_id=runtime.tool_call_id if runtime else "",
            name="send_report",
        )],
    }
    # Артефакт и текст отчёта — только при успешной доставке
    if result["ok"]:
        update["artifacts"] = [url]
        update["last_report"] = {"title": title, "markdown": markdown, "filename": fname}

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
    return json.dumps(list(enumerate(findings, start=1)), ensure_ascii=False)


@tool
def get_finding(index: int, runtime: ToolRuntime = None) -> str:
    """Конкретная запись из log_finding по номеру (номера — в выводе list_findings)."""
    state = runtime.state if runtime else {}
    findings = state.get("findings") or []
    if not 1 <= index <= len(findings):
        return f"Записи №{index} нет: всего записей {len(findings)}."
    return json.dumps(findings[index - 1], ensure_ascii=False)


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
        return json.dumps({"ok": True, "facts": facts}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({
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
        return json.dumps({"ok": True, "cases": cases}, ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps({
            "ok": False,
            "error": f"Поиск похожих дел недоступен: {type(exc).__name__}",
        }, ensure_ascii=False)


# ── Тулзы на агента ──────────────────────────────────────────────
# Скаут — MCP + журнал: увиденную улику подхватит следующая сторона
scout_tools = list(mcp_tools or []) + [log_finding, search_context_memory, search_similar_cases]
# Инвестигейтор — MCP + журнал расследования
investigator_tools = list(mcp_tools or []) + [
    log_finding,
    list_findings,
    get_finding,
    get_evidence,
    update_window,
    update_hypothesis,
    search_context_memory,
    search_similar_cases,
]
# Репортёр — отчёты, чтение журнала, Evidence и детерминированная выгрузка
extract_data_tool = next((item for item in mcp_tools if item.name == "extract_data"), None)
reporter_tools = [save_report, send_report, list_findings, get_finding, get_evidence]
if extract_data_tool is not None:
    reporter_tools.append(extract_data_tool)
# Чат-агент — без тулз
chat_tools: list = []
