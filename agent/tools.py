"""Тулзы агентов.

- MCP-тулзы Логикора (mcp_client).
- log_finding / list_findings / get_finding — журнал расследования.
- save_report / send_report — отчёты (файл в чате и callback-доставка).

Перечни тулз на каждого агента — в конце модуля.
"""

import datetime
import json
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from mcp_client import get_mcp_tools
from reporting import build_report, post_report

mcp_tools = get_mcp_tools()


@tool
def log_finding(
    finding: str,
    next_action: str,
    runtime: ToolRuntime = None,
) -> Command:
    """Зафиксировать результат анализа и следующий шаг.
    ОБЯЗАТЕЛЬНО вызывай после каждого осмысленного запроса в MCP.

    Аргументы:
    - finding: что нашёл (1–2 предложения)
    - next_action: что будешь делать дальше
    """
    ts = datetime.datetime.now(datetime.UTC).strftime("%H:%M:%S")
    note = f"[{ts}] {finding} → {next_action}"
    # ToolMessage обязателен: без ответа на tool_call ToolNode поднимет ValueError
    return Command(update={
        "findings": [note],
        "messages": [ToolMessage(
            content=note,
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
    return "\n".join(f"{i + 1}. {note}" for i, note in enumerate(findings))


@tool
def get_finding(index: int, runtime: ToolRuntime = None) -> str:
    """Конкретная запись из log_finding по номеру (номера — в выводе list_findings)."""
    state = runtime.state if runtime else {}
    findings = state.get("findings") or []
    if not 1 <= index <= len(findings):
        return f"Записи №{index} нет: всего записей {len(findings)}."
    return findings[index - 1]


# ── Тулзы на агента ──────────────────────────────────────────────
# У скаута — только MCP: он не фиксирует findings и не пишет отчёты
scout_tools = list(mcp_tools or [])
# Инвестигейтор — MCP + журнал расследования
investigator_tools = list(mcp_tools or []) + [log_finding, list_findings, get_finding]
# Репортёр — отчёты и чтение журнала
reporter_tools = [save_report, send_report, list_findings, get_finding]
# Чат-агент — без тулз
chat_tools: list = []
