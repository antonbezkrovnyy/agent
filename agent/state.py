"""Состояние внешнего workflow и вложенных агентов.

Диалог (messages) — то, что видит пользователь: только сообщения
пользователя и финальные ответы. Tool-циклы агентов живут в приватных каналах
(scout_msgs / inv_msgs / rep_msgs) и в диалог не попадают.
"""

import operator
from typing import Annotated, Sequence

from typing_extensions import NotRequired

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


class AgentWorkState(AgentState):
    """Внутреннее состояние create_agent: messages + поля для tools/промптов."""

    callback_url: NotRequired[str]
    artifacts: NotRequired[Annotated[list[str], operator.add]]
    findings: NotRequired[Annotated[list[dict], operator.add]]
    evidence: NotRequired[Annotated[list[dict], operator.add]]
    hypotheses: NotRequired[Annotated[list[dict], merge_hypotheses]]
    context: NotRequired[dict]
    last_report: NotRequired[dict]
    inv_summary: NotRequired[str]
    inv_result: NotRequired[dict]
    scout_note: NotRequired[str]
    scout_result: NotRequired[dict]
    active_skill: NotRequired[dict]
    skill_runs: NotRequired[Annotated[list[dict], operator.add]]
    anchors: NotRequired[list[dict]]
    visited_anchors: NotRequired[list[str]]
    memory_loaded: NotRequired[str]
    memory_error: NotRequired[str]
    context_memories: NotRequired[list[dict]]
    similar_cases: NotRequired[list[dict]]
    critic: NotRequired[dict]
    rework: NotRequired[int]
    run_tool_call_count: NotRequired[dict]
    progress: NotRequired[Annotated[dict, merge_progress]]


class State(MessagesState):
    callback_url: str
    artifacts: Annotated[list[str], operator.add]
    # Доказательства и записи только текущего расследования
    evidence: Annotated[list[dict], operator.add]
    findings: Annotated[list[dict], operator.add]
    hypotheses: Annotated[list[dict], merge_hypotheses]
    # ID, активное/base/focus-окна и история текущего расследования
    context: dict
    last_report: dict
    inv_summary: str
    inv_result: dict
    scout_note: str
    scout_result: dict
    report_result: dict
    intent: dict
    work_mode: str
    depth_history: list[dict]
    active_skill: dict
    skill_runs: Annotated[list[dict], operator.add]
    anchors: list[dict]
    visited_anchors: list[str]
    memory_candidates: list[dict]
    memory_loaded: str
    memory_saved: str
    memory_facts_saved: int
    memory_error: str
    context_memories: list[dict]
    similar_cases: list[dict]
    transition: str
    archive_done: int
    critic: dict
    rework: int
    tool_usage: dict
    progress: Annotated[dict, merge_progress]
    suggestions: list[dict]
    scout_msgs: Annotated[Sequence[BaseMessage], add_messages]
    inv_msgs: Annotated[Sequence[BaseMessage], add_messages]
    rep_msgs: Annotated[Sequence[BaseMessage], add_messages]
