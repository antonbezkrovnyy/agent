"""Состояние supervisor и приватных create_agent-агентов.

Общие каналы собраны в предметные mixin-группы, чтобы схемы не расходились.
В публичном messages находятся только сообщения пользователя и финальные ответы;
tool-циклы остаются в scout_msgs / inv_msgs / rep_msgs.
"""

import operator
from typing import Annotated, Sequence

from typing_extensions import TypedDict

from langchain.agents import AgentState
from langchain_core.messages import BaseMessage
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages

SCOUT_MSGS = "scout_msgs"
INV_MSGS = "inv_msgs"
REP_MSGS = "rep_msgs"


def merge_hypotheses(left: list[dict], right: list[dict]) -> list[dict]:
    merged = {item.get("id"): dict(item) for item in left or [] if item.get("id")}
    for item in right or []:
        if item.get("id"):
            merged[item["id"]] = dict(item)
    return list(merged.values())


def merge_work_items(left: list[dict], right: list[dict]) -> list[dict]:
    merged = {item.get("id"): dict(item) for item in left or [] if item.get("id")}
    for item in right or []:
        if item.get("id"):
            merged[item["id"]] = {**merged.get(item["id"], {}), **item}
    return list(merged.values())


def max_value(left: int, right: int) -> int:
    return max(left or 0, right or 0)


def merge_tool_usage(left: dict, right: dict) -> dict:
    merged = dict(left or {})
    for agent, count in (right or {}).items():
        merged[agent] = max(merged.get(agent, 0), count)
    return merged


def merge_progress(left: dict, right: dict) -> dict:
    if not left:
        return right
    if not right:
        return left
    if left.get("stage") == right.get("stage") and left.get("agent") == right.get("agent"):
        return {
            **left,
            **right,
            "tool_calls": max(left.get("tool_calls", 0), right.get("tool_calls", 0)),
        }
    return right


class EvidenceChannels(TypedDict):
    """Артефакты и проверяемые результаты текущего дела."""

    callback_url: str
    artifacts: Annotated[list[dict], operator.add]
    evidence: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[dict], operator.add]
    analysis_datasets: Annotated[list[dict], operator.add]
    analysis_results: Annotated[list[dict], operator.add]
    findings: Annotated[list[dict], operator.add]
    hypotheses: Annotated[list[dict], merge_hypotheses]


class CaseChannels(TypedDict):
    """Контекст, handoff и версионируемые playbooks текущего дела."""

    context: dict
    last_report: dict
    inv_summary: str
    inv_result: dict
    scout_note: str
    scout_result: dict
    active_skill: dict
    investigation_scope: dict
    control_overrides: dict
    skill_runs: Annotated[list[dict], operator.add]
    anchors: list[dict]
    visited_anchors: list[str]
    critic: dict


class MemoryChannels(TypedDict):
    """Материалы retrieval и аудит собственной памяти агента."""

    memory_loaded: str
    memory_operations: Annotated[list[dict], operator.add]
    memory_error: str
    context_memories: list[dict]
    similar_cases: list[dict]


class ProvenanceChannels(TypedDict):
    """Метаданные независимой проверки и происхождения данных."""

    independent_verification: dict
    verification_checks: Annotated[list[dict], operator.add]
    tenant: str
    source_endpoint: str
    schema_version: str
    time_semantics: str
    time_axes: dict


class AgentWorkState(AgentState, EvidenceChannels, CaseChannels, MemoryChannels, ProvenanceChannels):
    """Внутреннее состояние create_agent: messages и поля tools/промптов."""

    active_work_item: dict
    mcp_reuse_run_id: str
    work_mode: str
    depth_history: list[dict]
    investigation_plan: dict
    work_items: list[dict]
    run_metrics: list[dict]
    case_task: str
    rework: int
    run_tool_call_count: dict
    agent_phase: str
    continuation: bool
    termination_reason: str
    progress: Annotated[dict, merge_progress]


class State(MessagesState, EvidenceChannels, CaseChannels, MemoryChannels, ProvenanceChannels):
    """Checkpoint supervisor; бизнес-логика в этой схеме не размещается."""

    report_result: dict
    intent: dict
    mcp_reuse_run_id: str
    work_mode: str
    depth_history: list[dict]
    investigation_plan: dict
    work_items: Annotated[list[dict], merge_work_items]
    active_work_item: Annotated[dict, merge_progress]
    replan_requested: bool
    memory_candidates: list[dict]
    memory_saved: str
    memory_facts_saved: int
    evaluation: dict
    transition: str
    archive_done: int
    rework: Annotated[int, max_value]
    tool_usage: Annotated[dict, merge_tool_usage]
    run_metrics: Annotated[list[dict], operator.add]
    progress: Annotated[dict, merge_progress]
    suggestions: list[dict]
    scout_msgs: Annotated[Sequence[BaseMessage], add_messages]
    inv_msgs: Annotated[Sequence[BaseMessage], add_messages]
    rep_msgs: Annotated[Sequence[BaseMessage], add_messages]
