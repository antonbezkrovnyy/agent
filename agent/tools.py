"""Тулзы вложенных агентов.

- MCP-тулзы Логикора (mcp_client).
- log_finding / list_findings / get_finding — журнал расследования.
- save_report / send_report — отчёты (файл в чате и callback-доставка).

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

from mcp_client import get_mcp_tools
from reporting import build_report, post_report

mcp_tools = get_mcp_tools()
_MCP_TOOL_NAMES = {item.name for item in mcp_tools}
_WINDOWED_TOOLS = {
    "search_events",
    "count_events",
    "timeline",
    "top_values",
    "top_values_batch",
    "field_fill_rates",
    "duplicates",
}


def _result_status(data: dict) -> str:
    if data.get("ok") is False:
        return "error"
    coverage = data.get("time_coverage") or {}
    if data.get("complete") is False or coverage.get("complete") is False or data.get("truncated"):
        return "partial"
    return "ok"


@wrap_tool_call
async def normalize_tool_result(request, handler):
    """Зафиксировать окно, нормализовать content и сохранить Evidence."""
    name = request.tool_call["name"]
    if name not in _MCP_TOOL_NAMES:
        return await handler(request)

    args = dict(request.tool_call.get("args") or {})
    context = request.state.get("context") or {}
    if (name in _WINDOWED_TOOLS or "time_from" in args) and context.get("time_from") and context.get("time_to"):
        args["time_from"] = context["time_from"]
        args["time_to"] = context["time_to"]
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
    evidence = {
        "id": evidence_id,
        "tool": name,
        "arguments": args,
        "window": data.get("window") or {
            "from": args.get("time_from"),
            "to": args.get("time_to"),
            "source": "investigation-context",
        },
        "status": _result_status(data),
        "result": data,
        "error": data.get("error"),
    }
    message = message.model_copy(update={
        "content": json.dumps({"evidence_id": evidence_id, **data}, ensure_ascii=False)
    })
    return Command(update={"messages": [message], "evidence": [evidence]})


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

    note = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "claim": claim,
        "evidence_ids": evidence_ids,
        "next_action": next_action,
    }
    return Command(update={
        "findings": [note],
        "messages": [ToolMessage(
            content=json.dumps(note, ensure_ascii=False),
            tool_call_id=runtime.tool_call_id if runtime else "",
            name="log_finding",
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
    fname, url, nbytes = build_report(title, markdown)

    # Берём стейт напрямую из ToolRuntime
    state = runtime.state if runtime else {}
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


# ── Тулзы на агента ──────────────────────────────────────────────
# Скаут — MCP + журнал: увиденную улику подхватит следующая сторона
scout_tools = list(mcp_tools or []) + [log_finding]
# Инвестигейтор — MCP + журнал расследования
investigator_tools = list(mcp_tools or []) + [
    log_finding,
    list_findings,
    get_finding,
]
# Репортёр — отчёты и чтение журнала
reporter_tools = [save_report, send_report, list_findings, get_finding]
# Чат-агент — без тулз
chat_tools: list = []
