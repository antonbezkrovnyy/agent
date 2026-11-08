"""Supervisor диалога и investigation/reporting-субграфов LogiCore."""

from datetime import UTC, datetime, timedelta

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.types import Overwrite

from agents import build_agents, node_chat
from export import node_export
from investigation import node_archive, node_intent
from investigation_graph import build_investigation_graph
from memory import node_memory_context, node_memory_inspect, node_memory_load, node_memory_retrieve, node_memory_save
from progress import simple_progress
from quick_graph import build_quick_graph
from reporting_graph import build_reporting_graph
from subgraph import invoke_subgraph
from config import Settings
from state import State

settings = Settings()


def entry(state: State) -> dict:
    """Очистить run-scoped каналы, сохранив данные текущего случая."""
    from uuid import uuid4

    if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
        return {
            "mcp_reuse_run_id": uuid4().hex,
            "run_deadline_at": (
                datetime.now(UTC) + timedelta(seconds=settings.agent_run_timeout_seconds)
            ).isoformat(timespec="milliseconds"),
            "artifacts": Overwrite([]),
            "scout_msgs": Overwrite([]),
            "inv_msgs": Overwrite([]),
            "rep_msgs": Overwrite([]),
            "intent": {},
            "scout_note": "",
            "scout_result": {},
            "report_result": {},
            "critic": {},
            "rework": Overwrite(0),
            "control_overrides": {
                "_step": (state.get("control_overrides") or {}).get("_step") or {},
            },
            "active_work_item": Overwrite({}),
            "replan_requested": False,
            "tool_usage": {},
            "progress": simple_progress("intent", "Определяю задачу"),
            "suggestions": [],
            "memory_candidates": [],
            "memory_loaded": "",
            "memory_saved": "",
            "memory_facts_saved": 0,
            "memory_operations": Overwrite([]),
            "memory_error": "",
            "evaluation": {},
            "context_memories": [],
            "similar_cases": [],
            "archive_done": 0,
        }
    return {}


def pick_agent(state: State) -> str:
    intent = state.get("intent") or {}
    if intent.get("remember_fact"):
        return "reporting"
    if intent.get("needs_new_data"):
        return "deep"
    if intent.get("edit_existing_report"):
        return "reporting"
    if intent.get("label") == "continuation" and state.get("last_report"):
        return "reporting"
    if intent.get("label") == "complex":
        return "deep"
    return "chat"


def route_to_work(state: State) -> str:
    intent = state.get("intent") or {}
    if intent.get("export_data"):
        return "export"
    if not intent.get("needs_new_data"):
        return pick_agent(state)
    return "deep" if intent.get("work_mode") == "deep" else "quick"


def route_after_intent(state: State) -> str:
    intent = state.get("intent") or {}
    if intent.get("inspect_memory"):
        return "memory_inspect"
    if not state.get("context") and intent.get("related_investigation_id"):
        return "memory_load"
    if not intent.get("needs_new_data") and not intent.get("export_data"):
        return pick_agent(state)
    has_history = bool(state.get("evidence") or state.get("findings") or state.get("last_report"))
    if has_history and state.get("transition") == "new":
        return "archive"
    return "memory_retrieve" if intent.get("needs_new_data") else route_to_work(state)


def route_after_restore(state: State) -> str:
    return "memory_retrieve" if (state.get("intent") or {}).get("needs_new_data") else route_to_work(state)


def route_after_quick(state: State) -> str:
    escalation = (state.get("scout_result") or {}).get("escalation") or {}
    return "deep" if escalation.get("needed") else "reporting"


def route_after_reporting(state: State) -> str:
    critic = state.get("critic") or {}
    if critic.get("verdict") == "rework_data" and state.get("rework", 0) <= settings.max_rework:
        return "deep"
    return "memory_save"


def build_graph(checkpointer=None):
    """Собрать supervisor; вложенные графы используют checkpoint основного треда."""
    build_agents()
    quick_graph = build_quick_graph()
    deep_graph = build_investigation_graph(settings)
    reporting_graph = build_reporting_graph(settings)

    async def quick_node(state):
        return await invoke_subgraph(quick_graph, state)

    async def deep_node(state):
        return await invoke_subgraph(deep_graph, state)

    async def reporting_node(state):
        return await invoke_subgraph(reporting_graph, state)

    builder = StateGraph(State)
    builder.add_node("entry", entry)
    builder.add_node("memory_context", node_memory_context)
    builder.add_node("intent", node_intent)
    builder.add_node("memory_load", node_memory_load)
    builder.add_node("memory_inspect", node_memory_inspect)
    builder.add_node("memory_retrieve", node_memory_retrieve)
    builder.add_node("memory_save", node_memory_save)
    builder.add_node("archive", node_archive)
    builder.add_node("quick", quick_node)
    builder.add_node("deep", deep_node)
    builder.add_node("reporting", reporting_node)
    builder.add_node("chat", node_chat)
    builder.add_node("export", node_export)

    builder.add_edge(START, "entry")
    builder.add_edge("entry", "memory_context")
    builder.add_edge("memory_context", "intent")
    builder.add_conditional_edges(
        "intent",
        route_after_intent,
        ["memory_load", "memory_inspect", "memory_retrieve", "archive", "export", "quick", "chat", "deep", "reporting"],
    )
    builder.add_conditional_edges(
        "memory_load",
        route_after_restore,
        ["memory_retrieve", "export", "quick", "chat", "deep", "reporting"],
    )
    builder.add_conditional_edges(
        "archive",
        route_after_restore,
        ["memory_retrieve", "export", "quick", "chat", "deep", "reporting"],
    )
    builder.add_conditional_edges(
        "memory_retrieve",
        route_to_work,
        ["export", "quick", "chat", "deep", "reporting"],
    )
    builder.add_conditional_edges("quick", route_after_quick, ["deep", "reporting"])
    builder.add_edge("deep", "reporting")
    builder.add_conditional_edges("reporting", route_after_reporting, ["deep", "memory_save"])
    builder.add_edge("memory_save", END)
    builder.add_edge("memory_inspect", END)
    builder.add_edge("chat", END)
    builder.add_edge("export", "memory_save")

    return builder.compile(checkpointer=checkpointer)


graph = build_graph()
