"""Граф агента: интент + скаут параллельно, затем агент по интенту, критик.

Структура (параллельные ветки — по паттерну state-reducers из
LangChain Academy module-2):
- entry — сбрасывает служебные каналы текущего run'а и пишет в диалог
  служебное сообщение;
- intent и scout — параллельные ветки: интент классифицирует сообщение
  (simple/complex/continuation) и кладёт результат в канал intent;
  скаут делает 1–2 быстрых запроса в MCP и кладёт короткий ориентир в
  канал scout_note. Ветки не сходятся в ноде-барьере: каждая пишет в
  свои каналы, а дальше идёт та, что по построению заканчивается
  последней, — скаут (у него tool-цикл, у интента один вызов LLM);
- scout → выбор агента по интенту и контексту стейта (continuation:
  есть last_report → репортёр, есть findings → инвестигейтор, иначе чат);
- chat / investigator / reporter — агенты со своими промптами
  (prompts/*.md) и тулзами (tools.py); tool-цикл каждого — в приватном
  канале стейта, в диалог попадают только служебные сообщения и
  финальный ответ;
- критик — после инвестигейтора (улики) и после репортёра (отчёт);
  rework_data возвращает на добор данных, rework_report — на доработку
  отчёта; не больше max_rework раз за run
  (settings.critic_enabled отключает его полностью).

findings, last_report и inv_summary живут весь тред — нужны для правок
отчёта и продолжения расследования.
"""

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.types import Overwrite

from agents import (
    SVC_START,
    _intent_heuristic,
    _service,
    node_chat,
    node_critic_evidence,
    node_critic_report,
    node_investigator,
    node_intent,
    node_reporter,
    node_scout,
)
from config import Settings
from state import INV_MSGS, REP_MSGS, SCOUT_MSGS, State
from tools import investigator_tools, reporter_tools, scout_tools

settings = Settings()


# ── Ноды ─────────────────────────────────────────────────────────

def entry(state: State) -> dict:
    """Сбрасывает служебные каналы и артефакты при новом сообщении.

    Overwrite обходит редьюсер — артефакты прошлых run'ов не
    прилепляются к новому ответу, приватные tool-циклы чистятся.
    """
    if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
        return {
            "messages": [_service(SVC_START)],
            "artifacts": Overwrite([]),
            "scout_msgs": Overwrite([]),
            "inv_msgs": Overwrite([]),
            "rep_msgs": Overwrite([]),
            "intent": {},
            "scout_note": "",
            "critic": {},
            "rework": 0,
            "intro": 0,
        }
    return {}


# ── Маршрутизация ────────────────────────────────────────────────

def _has_tool_calls(state: State, work_key: str) -> bool:
    """Агент крутит tool-цикл: последнее в его приватном канале —
    ответ с tool_calls (ToolMessage и пустой канал — цикл закончен)."""
    last = (state.get(work_key) or [None])[-1]
    return bool(getattr(last, "tool_calls", None))


def pick_agent(state: State) -> str:
    """Выбор агента: по интенту; continuation — по контексту стейта."""
    label = (state.get("intent") or {}).get("label")
    if not label:
        # Редкая гонка: скаут завершился раньше интента — берём эвристику
        label = _intent_heuristic(state)["label"]
    if label == "complex":
        return "investigator"
    if label == "continuation":
        if state.get("last_report"):
            return "reporter"
        if state.get("findings"):
            return "investigator"
        return "chat"
    return "chat"


def route_after_scout(state: State) -> str:
    if _has_tool_calls(state, SCOUT_MSGS):
        return "scout_tools"
    return pick_agent(state)


def route_after_investigator(state: State) -> str:
    if _has_tool_calls(state, INV_MSGS):
        return "inv_tools"
    if settings.critic_enabled:
        return "critic_evidence"
    return "reporter"


def route_after_critic_evidence(state: State) -> str:
    critic = state.get("critic") or {}
    if (
        critic.get("verdict") == "rework_data"
        and state.get("rework", 0) <= settings.max_rework
    ):
        return "investigator"
    return "reporter"


def route_after_reporter(state: State) -> str:
    if _has_tool_calls(state, REP_MSGS):
        return "rep_tools"
    # Критик отчёта — только если репортёр реально отчёт создал
    if settings.critic_enabled and state.get("artifacts"):
        return "critic_report"
    return END


def route_after_critic_report(state: State) -> str:
    critic = state.get("critic") or {}
    verdict = critic.get("verdict")
    if (
        verdict in ("rework_report", "rework_data")
        and state.get("rework", 0) <= settings.max_rework
    ):
        return "reporter" if verdict == "rework_report" else "investigator"
    return END


# ── Сборка ───────────────────────────────────────────────────────

def build_graph(checkpointer=None):
    """checkpointer — опционально: в agra персистентность своя,
    в локальных тестах передаётся InMemorySaver."""
    builder = StateGraph(State)

    builder.add_node("entry", entry)
    builder.add_node("intent", node_intent)
    builder.add_node("scout", node_scout)
    builder.add_node("scout_tools", ToolNode(scout_tools, messages_key=SCOUT_MSGS))
    builder.add_node("chat", node_chat)
    builder.add_node("investigator", node_investigator)
    builder.add_node("inv_tools", ToolNode(investigator_tools, messages_key=INV_MSGS))
    builder.add_node("reporter", node_reporter)
    builder.add_node("rep_tools", ToolNode(reporter_tools, messages_key=REP_MSGS))
    builder.add_node("critic_evidence", node_critic_evidence)
    builder.add_node("critic_report", node_critic_report)

    builder.add_edge(START, "entry")
    # Параллельные ветки: интент и скаут стартуют одновременно
    builder.add_edge("entry", "intent")
    builder.add_edge("entry", "scout")
    # Интент кладёт результат в канал intent и закрывается;
    # дальше идёт скаут (по построению — последняя ветка)
    builder.add_edge("intent", END)
    builder.add_conditional_edges(
        "scout",
        route_after_scout,
        ["scout_tools", "chat", "investigator", "reporter"],
    )
    builder.add_edge("scout_tools", "scout")
    builder.add_edge("chat", END)

    builder.add_conditional_edges(
        "investigator",
        route_after_investigator,
        ["inv_tools", "critic_evidence", "reporter"],
    )
    builder.add_edge("inv_tools", "investigator")
    builder.add_conditional_edges(
        "critic_evidence",
        route_after_critic_evidence,
        ["investigator", "reporter"],
    )

    builder.add_conditional_edges(
        "reporter",
        route_after_reporter,
        ["rep_tools", "critic_report", END],
    )
    builder.add_edge("rep_tools", "reporter")
    builder.add_conditional_edges(
        "critic_report",
        route_after_critic_report,
        ["reporter", "investigator", END],
    )

    return builder.compile(checkpointer=checkpointer)


graph = build_graph()
