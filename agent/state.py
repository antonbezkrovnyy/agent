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


class AgentWorkState(AgentState):
    """Внутреннее состояние create_agent: messages + поля для tools/промптов."""

    callback_url: NotRequired[str]
    artifacts: NotRequired[Annotated[list[str], operator.add]]
    findings: NotRequired[Annotated[list[dict], operator.add]]
    evidence: NotRequired[Annotated[list[dict], operator.add]]
    context: NotRequired[dict]
    last_report: NotRequired[dict]
    inv_summary: NotRequired[str]
    inv_result: NotRequired[dict]
    scout_note: NotRequired[str]
    scout_result: NotRequired[dict]
    critic: NotRequired[dict]
    rework: NotRequired[int]


class State(MessagesState):
    callback_url: str
    artifacts: Annotated[list[str], operator.add]
    # Доказательства и записи только текущего расследования
    evidence: Annotated[list[dict], operator.add]
    findings: Annotated[list[dict], operator.add]
    # Зафиксированные id и временное окно текущего расследования
    context: dict
    last_report: dict
    inv_summary: str
    inv_result: dict
    scout_note: str
    scout_result: dict
    report_result: dict
    intent: dict
    critic: dict
    rework: int
    scout_msgs: Annotated[Sequence[BaseMessage], add_messages]
    inv_msgs: Annotated[Sequence[BaseMessage], add_messages]
    rep_msgs: Annotated[Sequence[BaseMessage], add_messages]
