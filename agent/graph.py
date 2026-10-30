"""Граф агента: LLM + MCP-инструменты Earth + отчёты (файл в чате и callback-доставка).

Улучшения:
- Артефакты храним в State через редьюсер, убираем хрупкий перебор сообщений.
- Нода entry сбрасывает артефакты/находки при новом сообщении пользователя.
- Тулз log_finding: принудительная фиксация хода расследования.
- Асинхронная доставка отчётов с ретраями (tenacity).
- ToolRuntime используется напрямую, без getattr-костылей.
"""

import base64
import datetime
import httpx
import json
import operator
import re
from typing import Annotated, Any, Sequence

from typing_extensions import TypedDict

from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
)
from langchain_core.tools import tool
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, ToolRuntime, tools_condition
from langgraph.types import Command, Overwrite
from tenacity import retry, stop_after_attempt, wait_exponential

from mcp_client import get_mcp_tools
from config import Settings

settings = Settings()

# ──────────────────────────────────────────────
# 1. LLM
# ──────────────────────────────────────────────
llm = init_chat_model(
    model_provider="openai",
    model=settings.llm_model,
    base_url=settings.llm_base_url,
    api_key=settings.llm_token,
    temperature=0.0,
    max_retries=3,
)

# ──────────────────────────────────────────────
# 2. Системный промпт
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """\
Ты — инженер-аналитик по журналу событий Earth (Логикор).

Правила:
- Все факты о событиях — только через инструменты (search_events, count_events,
  timeline, top_values, field_lookup, get_event и др.). Прикидывать цифры в уме нельзя.
- Время «сейчас» бери из server_time, окна — от него.

Алгоритм расследования (если задача — расследование аномалии):
1. Определи время и контекст аномалии из входящего сообщения.
2. Выполни поисковые запросы через MCP-инструменты.
3. ПОСЛЕ КАЖДОГО осмысленного запроса ОБЯЗАТЕЛЬНО вызови
   log_finding(finding, next_action) — зафиксируй, что нашёл и что дальше.
4. Повторяй шаги 2–3, пока не соберёшь достаточно улик.
5. Сформируй markdown-отчёт и доставь его.

Отчёты:
- Если пользователь просит отчёт / сохранить файл — вызови save_report(title, markdown).
- Если задача — расследование аномалии (вебхук, «расследуй») — вызови
  send_report(title, markdown). callback_url уже в данных запуска.
- НЕ вызывай save_report и send_report одновременно.
- Если send_report вернул ошибку — вызови save_report.
"""

# ──────────────────────────────────────────────
# 3. Утилиты отчётов
# ──────────────────────────────────────────────
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(value: str, fallback: str) -> str:
    segment = _SAFE_SEGMENT.sub("-", value.strip()).strip("-.")
    if not any(c.isascii() and c.isalpha() for c in segment):
        return fallback
    return segment or fallback


def _build_report(title: str, markdown: str, filename: str | None = None) -> tuple[str, str, int]:
    """Собирает (имя файла, data-URL отчёта, размер в байтах)."""
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


# ──────────────────────────────────────────────
# 4. State
# ──────────────────────────────────────────────
class State(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    callback_url: str
    # Артефакты (data-URL отчётов) текущего run'а
    artifacts: Annotated[list[str], operator.add]
    # Сжатые находки расследования (из log_finding)
    findings: Annotated[list[str], operator.add]


# ──────────────────────────────────────────────
# 5. Тулзы
# ──────────────────────────────────────────────
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
    fname, url, nbytes = _build_report(title, markdown, filename)
    payload = json.dumps({"ok": True, "filename": fname, "bytes": nbytes}, ensure_ascii=False)
    return Command(update={
        "messages": [ToolMessage(
            content=payload,
            tool_call_id=runtime.tool_call_id if runtime else "",
            name="save_report",
        )],
        "artifacts": [url],
    })


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def _post_report(target: str, payload: dict) -> httpx.Response:
    """POST с ретраями: 3 попытки, экспоненциальная задержка 1с → 2с → 4с."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        return await client.post(target, json=payload)


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
    fname, url, nbytes = _build_report(title, markdown)

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
            resp = await _post_report(target, payload)
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
    # Артефакт только при успешной доставке
    if result["ok"]:
        update["artifacts"] = [url]

    return Command(update=update)


tools = list(mcp_tools or []) + [save_report, send_report, log_finding]
llm_with_tools = llm.bind_tools(tools)


# ──────────────────────────────────────────────
# 6. Ноды
# ──────────────────────────────────────────────
def entry(state: State) -> dict:
    """Сбрасывает артефакты и находки при новом пользовательском сообщении.
    Это обеспечивает границу run'а: отчёты прошлых run'ов не прилепляются к новому ответу.
    """
    if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
        # Overwrite обходит редьюсер и ставит канал в пустой список
        return {"artifacts": Overwrite([]), "findings": Overwrite([])}
    return {}


def node_llm(state: State) -> dict:
    # Подмешиваем накопленные находки в системный промпт
    system = SYSTEM_PROMPT
    if state.get("findings"):
        findings_text = "\n".join(state["findings"])
        system += f"\n\nСобранные находки:\n{findings_text}"

    messages: list[BaseMessage] = [SystemMessage(content=system), *state["messages"]]
    response = llm_with_tools.invoke(messages)

    # Финальный ответ (без tool_calls) забирает артефакты из State
    if not getattr(response, "tool_calls", None) and state.get("artifacts"):
        response.artifacts = list(state["artifacts"])

    return {"messages": [response]}


# ──────────────────────────────────────────────
# 7. Сборка графа
# ──────────────────────────────────────────────
def build_graph():
    builder = StateGraph(State)

    builder.add_node("entry", entry)
    builder.add_node("llm", node_llm)
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "entry")
    builder.add_edge("entry", "llm")
    builder.add_conditional_edges("llm", tools_condition, ["tools", END])
    builder.add_edge("tools", "llm")

    return builder.compile()


graph = build_graph()