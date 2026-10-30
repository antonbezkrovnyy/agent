"""Граф агента: LLM + MCP-инструменты Earth + отчёты (файл в чате и callback-доставка).

Отчёты: тул save_report складывает data-URL в state["reports"]; финальное
AIMessage получает их в поле artifacts — UI (ArtifactsDownload в ai.tsx)
показывает кнопку скачивания прямо в чате (паттерн pack_agent/packing-ui).
Расследования: тул send_report доставляет отчёт на callback_url из данных
запуска (вебхук Логикора несёт callback_url в payload) и одновременно
прикрепляет файл к ответу для просмотра в UI.
"""

import base64
import datetime
import httpx
import json
import operator
import re
from typing import Annotated, Any, Sequence, TypedDict
from typing_extensions import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, ToolRuntime
from langgraph.types import Command

from mcp_client import get_mcp_tools
from config import Settings
settings = Settings()

# 1. Инициализация LLM (Явный контракт, production-ready настройки)
llm = init_chat_model(
    model_provider='openai',
    model=settings.llm_model,
    base_url=settings.llm_base_url,
    api_key=settings.llm_token,
    temperature=0.0,          # Детерминизм для предсказуемости
    max_retries=3             # Устойчивость к сетевым сбоям провайдера
)

SYSTEM_PROMPT = """\
Ты — инженер-аналитик по журналу событий Earth (Логикор).
Все факты о событиях — только через инструменты (search_events, count_events,
timeline, top_values, field_lookup, get_event и др.); прикидывать цифры в уме нельзя.
Время «сейчас» бери из server_time, окна — от него.

Когда пользователь просит отчёт, сохранить результаты файлом или выгрузить данные —
сформируй markdown-отчёт (вердикты, числа с окнами, uuid-якоря) и вызови тул
save_report(title, markdown, filename?). Файл прикрепится к следующему за вызовом
ответу автоматически: кнопку скачивания НЕ описывай и не выдумывай ссылки — просто
назови файл в финальном ответе. В режиме расследования (ниже) этот тул не нужен —
вместо него работает send_report.

Если входящее сообщение — задача расследования аномалии (пометка вебхука/автоматического
запуска, «расследуй», «аномалия», «нарушение»), проведи полное расследование инструментами
и на финише вызови send_report(title, markdown): отчёт будет доставлен на принимающий
эндпоинт — callback_url уже задан в данных запуска, URL выдумывать и пересказывать нельзя.
В этом режиме НЕ вызывай save_report — send_report сам прикрепляет отчёт файлом к ответу,
двойной вызов даст два одинаковых файла. Финальный ответ в таком режиме — краткий вердикт
(2–4 предложения). Если send_report вернул ошибку (эндпоинт не задан или доставка не
удалась) — вызови save_report и в финальном ответе укажи, что доставка не выполнена.
"""

# 2. Инструменты Логикора (MCP, streamable-http — ADR-0010), инициализация один раз
mcp_tools = get_mcp_tools()

# 2b. Тул отчётов: data-URL в state["reports"], к финальному сообщению пришьёт node_llm
REPORTS_CLEAR = {"__clear__": True}


def _merge_reports(existing: Any, new: Any) -> list:
    """Конкатенация data-URL отчётов; маркер REPORTS_CLEAR очищает список (ревизия 2026-10-29)."""
    result = list(existing or [])
    for item in new or []:
        if isinstance(item, dict) and item.get("__clear__"):
            result = []
        else:
            result.append(str(item))
    return result


_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(value: str, fallback: str) -> str:
    segment = _SAFE_SEGMENT.sub("-", value.strip()).strip("-.")
    # Без латиницы в имени (кириллический заголовок) — фолбэк, чтобы не получить «24.md»
    if not any(c.isascii() and c.isalpha() for c in segment):
        return fallback
    return segment or fallback


def _build_report(title: str, markdown: str, filename: str | None = None) -> tuple[str, str, int]:
    """Собирает (имя файла, data-URL отчёта, размер в байтах). Общий для save_report и send_report."""
    name = _slug(filename, "report") if filename else _slug(title, "report")
    if not name.lower().endswith(".md"):
        name += ".md"
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H-%M-%S")
    fname = f"{stamp}-{name}"
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    body = (
        "---\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"saved_at: {now}\n"
        "---\n\n" + markdown.rstrip() + "\n"
    )
    data = body.encode("utf-8")
    url = f"data:text/markdown;base64,{base64.b64encode(data).decode('ascii')}#{fname}"
    return fname, url, len(data)


def _tool_msg(name: str, content: str, runtime: ToolRuntime = None) -> ToolMessage:
    return ToolMessage(
        content=content, tool_call_id=runtime.tool_call_id if runtime is not None else "", name=name
    )


@tool
def save_report(
    title: str,
    markdown: str,
    filename: str | None = None,
    runtime: ToolRuntime = None,
) -> Command:
    """Сохранить отчёт расследования файлом, который пользователь скачает из чата.

    Когда вызывать: просят «отчёт», «сохрани/выгрузи в файл», «скачай» — по итогам анализа.
    Аргументы:
    - title: заголовок отчёта; идёт в front matter и в имя файла, если filename не задан.
    - markdown: тело отчёта (вердикты, числа с окнами, uuid-якоря). Пишется как есть.
    - filename: опционально, имя файла (без расширения — добавится .md).
    Файл прикрепится к следующему за вызовом ответу автоматически: кнопку скачивания
    показывать не нужно, просто назови файл в финальном ответе.
    Ответ: {"ok": true, "filename": "<имя>.md", "bytes": N}.
    """
    fname, url, nbytes = _build_report(title, markdown, filename)
    payload = json.dumps({"ok": True, "filename": fname, "bytes": nbytes}, ensure_ascii=False)
    return Command(
        update={
            "reports": [url],
            "messages": [_tool_msg("save_report", payload, runtime)],
        }
    )


@tool
def send_report(
    title: str,
    markdown: str,
    callback_url: str | None = None,
    runtime: ToolRuntime = None,
) -> Command:
    """Доставить готовый отчёт расследования на принимающий эндпоинт (callback).

    Когда вызывать: задача — расследование аномалии (сообщение помечено как вебхук/
    автоматический запуск, «расследуй») и анализ завершён — результат нужно доставить
    получателю, а не только показать в чате.
    Аргументы:
    - title: заголовок отчёта.
    - markdown: тело отчёта (вердикты, числа с окнами, uuid-якоря).
    - callback_url: принимающий URL; обычно НЕ нужен — эндпоинт уже в данных запуска,
      явное значение переопределяет его.
    Отчёт также прикрепится файлом к следующему за вызовом ответу.
    Ответ: {"ok": true, "status": N, "filename": ...} — доставлено;
    {"ok": false, "error": "..."} — доставка не выполнена (файл всё равно прикреплён).
    """
    fname, url, nbytes = _build_report(title, markdown)
    target = (callback_url or "").strip()
    if not target and runtime is not None:
        state = getattr(runtime, "state", None)
        if isinstance(state, dict):
            target = str(state.get("callback_url") or "").strip()
    result: dict[str, Any] = {"filename": fname, "bytes": nbytes}
    if not target:
        result["ok"] = False
        result["error"] = "callback_url не задан: эндпоинт отсутствует в данных запуска"
    else:
        thread_id = None
        config = getattr(runtime, "config", None) or {}
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
            resp = httpx.post(target, json=payload, timeout=30.0)
            result["status"] = resp.status_code
            result["ok"] = 200 <= resp.status_code < 300
            if not result["ok"]:
                result["error"] = f"эндпоинт вернул {resp.status_code}: {resp.text[:300]}"
        except Exception as exc:
            result["ok"] = False
            result["error"] = f"POST не удался: {type(exc).__name__}: {exc}"
    # Доставленный отчёт — единственный файл запуска: сбрасываем накопленные раньше
    # (например, ошибочным save_report), чтобы к ответу не прилепилось два файла.
    return Command(
        update={
            "reports": [REPORTS_CLEAR, url],
            "messages": [_tool_msg("send_report", json.dumps(result, ensure_ascii=False), runtime)],
        }
    )


tools = list(mcp_tools or []) + [save_report, send_report]
llm_with_tools = llm.bind_tools(tools) if tools else llm

# 3. Схема состояния (Строгий контракт для agent-chat-ui и PostgreSQL чекпоинтов)
class State(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    reports: Annotated[list, _merge_reports]  # data-URL отчётов текущего запуска
    callback_url: str  # эндпоинт доставки отчёта, задаётся input запуска (вебхук)


# 4. Узел предобработки: сброс отчётов, не прикреплённых предыдущим (упавшим) запуском
def node_preprocess(state: State) -> dict:
    return {"reports": [REPORTS_CLEAR]}


# 5. Узел с реальным вызовом LLM
def node_llm(state: State) -> dict:
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
    response = llm_with_tools.invoke(messages)

    update: dict = {"messages": [response]}
    # Финальный ответ (без tool_calls) забирает накопленные отчёты в message.artifacts
    if not getattr(response, "tool_calls", None):
        pending = state.get("reports") or []
        if pending:
            response.artifacts = list(pending)
            update["reports"] = [REPORTS_CLEAR]
    return update


def should_continue(state: State) -> str:
    """Роутер: в ответе LLM есть tool_calls — к узлу tools, иначе — завершение."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return END


# 6. Сборка графа (Декларативное описание конечного автомата)
def build_graph():
    builder = StateGraph(State)

    # Регистрация узлов
    builder.add_node("preprocess", node_preprocess)
    builder.add_node("llm", node_llm)
    if tools:
        builder.add_node("tools", ToolNode(tools))

    # Определение потока выполнения (Edges)
    builder.add_edge(START, "preprocess")
    builder.add_edge("preprocess", "llm")
    if tools:
        builder.add_conditional_edges("llm", should_continue, ["tools", END])
        builder.add_edge("tools", "llm")  # результат инструмента — обратно к LLM
    else:
        builder.add_edge("llm", END)

    return builder.compile()

# Глобальный экземпляр скомпилированного графа для регистрации в Aegra
graph = build_graph()
