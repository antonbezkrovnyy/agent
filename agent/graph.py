"""Workflow расследования Логикора.

- entry очищает run-scoped каналы;
- intent выбирает маршрут и фиксирует окно нового расследования;
- scout запускается только при необходимости новых данных;
- investigator и reporter возвращают structured response;
- middleware сохраняет Evidence с фактическими аргументами и результатами;
- критики проверяют Evidence, включая обычные текстовые ответы;
- rework_data и rework_report ограничены settings.max_rework.

Новое расследование получает чистые evidence/findings. Правка отчёта
использует прежние данные и last_report.
"""

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.types import Overwrite

from agents import (
    build_agents,
    node_chat,
    node_investigator,
    node_reporter,
    node_scout,
)
from critics import node_critic_evidence, node_critic_report
from export import node_export
from investigation import node_archive, node_confirm_investigation, node_intent
from progress import simple_progress
from config import Settings
from state import State

settings = Settings()


# ── Ноды ─────────────────────────────────────────────────────────

def entry(state: State) -> dict:
    """Сбрасывает служебные каналы и артефакты при новом сообщении.

    Overwrite обходит редьюсер — артефакты прошлых run'ов не
    прилепляются к новому ответу, приватные tool-циклы чистятся.
    """
    if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
        return {
            "artifacts": Overwrite([]),
            "scout_msgs": Overwrite([]),
            "inv_msgs": Overwrite([]),
            "rep_msgs": Overwrite([]),
            "intent": {},
            "scout_note": "",
            "scout_result": {},
            "inv_result": {},
            "report_result": {},
            "critic": {},
            "rework": 0,
            "tool_usage": {},
            "progress": simple_progress("intent", "Определяю задачу"),
            "suggestions": [],
            "archive_done": 0,
        }
    return {}


# ── Маршрутизация ────────────────────────────────────────────────

def pick_agent(state: State) -> str:
    intent = state.get("intent") or {}
    if intent.get("needs_new_data"):
        return "investigator"
    if intent.get("edit_existing_report") and state.get("last_report"):
        return "reporter"
    if intent.get("label") == "continuation" and state.get("last_report"):
        return "reporter"
    if intent.get("label") == "complex":
        return "investigator"
    return "chat"


def route_to_work(state: State) -> str:
    intent = state.get("intent") or {}
    if intent.get("export_data"):
        return "export"
    if not intent.get("needs_new_data"):
        return pick_agent(state)
    if intent.get("label") == "continuation" and state.get("context"):
        return "investigator"
    return "scout"


def route_after_intent(state: State) -> str:
    intent = state.get("intent") or {}
    if not intent.get("needs_new_data") and not intent.get("export_data"):
        return pick_agent(state)
    has_history = bool(state.get("evidence") or state.get("findings") or state.get("last_report"))
    if has_history and state.get("transition") in ("new", "uncertain"):
        return "confirm_investigation"
    return route_to_work(state)


def route_after_confirm(state: State) -> str:
    if state.get("transition") in ("new", "new_thread"):
        return "archive"
    return route_to_work(state)


def route_after_archive(state: State) -> str:
    if state.get("transition") == "new_thread":
        return END
    return route_to_work(state)


def route_after_investigator(state: State) -> str:
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
    if settings.critic_enabled and state.get("report_result"):
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
    build_agents()
    builder = StateGraph(State)

    builder.add_node("entry", entry)
    builder.add_node("intent", node_intent)
    builder.add_node("archive", node_archive)
    builder.add_node("confirm_investigation", node_confirm_investigation)
    builder.add_node("scout", node_scout)
    builder.add_node("chat", node_chat)
    builder.add_node("investigator", node_investigator)
    builder.add_node("reporter", node_reporter)
    builder.add_node("export", node_export)
    builder.add_node("critic_evidence", node_critic_evidence)
    builder.add_node("critic_report", node_critic_report)

    builder.add_edge(START, "entry")
    builder.add_edge("entry", "intent")
    builder.add_conditional_edges(
        "intent",
        route_after_intent,
        ["confirm_investigation", "export", "scout", "chat", "investigator", "reporter"],
    )
    builder.add_conditional_edges(
        "confirm_investigation",
        route_after_confirm,
        ["archive", "export", "scout", "chat", "investigator", "reporter"],
    )
    builder.add_conditional_edges(
        "archive",
        route_after_archive,
        ["export", "scout", "chat", "investigator", "reporter", END],
    )
    builder.add_conditional_edges(
        "scout",
        pick_agent,
        ["chat", "investigator", "reporter"],
    )
    builder.add_edge("chat", END)
    builder.add_edge("export", END)

    builder.add_conditional_edges(
        "investigator",
        route_after_investigator,
        ["critic_evidence", "reporter"],
    )
    builder.add_conditional_edges(
        "critic_evidence",
        route_after_critic_evidence,
        ["investigator", "reporter"],
    )

    builder.add_conditional_edges(
        "reporter",
        route_after_reporter,
        ["critic_report", END],
    )
    builder.add_conditional_edges(
        "critic_report",
        route_after_critic_report,
        ["reporter", "investigator", END],
    )

    return builder.compile(checkpointer=checkpointer)


graph = build_graph()
