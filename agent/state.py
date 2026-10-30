"""Каналы общего State.

Только данные — поведение описано в agents.py и graph.py.

Диалог (messages) — то, что видит пользователь: сообщения пользователя,
служебные сообщения переходов, объединённое сообщение «интент + скаут»
и финальный ответ. Tool-циклы агентов живут в приватных каналах
(scout_msgs / inv_msgs / rep_msgs) и в диалог не попадают.
"""

import operator
from typing import Annotated, Sequence

from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

# Приватные tool-циклы агентов (ToolNode ходит по своему messages_key)
SCOUT_MSGS = "scout_msgs"
INV_MSGS = "inv_msgs"
REP_MSGS = "rep_msgs"


class State(TypedDict):
    # Диалог, видимый пользователю
    messages: Annotated[Sequence[BaseMessage], add_messages]
    # Эндпоинт для доставки отчёта (из данных запуска)
    callback_url: str
    # Артефакты (data-URL отчётов) текущего run'а; сбрасываются в entry
    artifacts: Annotated[list[str], operator.add]
    # Записи расследования (log_finding), живут весь тред
    findings: Annotated[list[str], operator.add]
    # Последний отчёт {title, markdown, filename} — для правок, весь тред
    last_report: dict
    # Резюме последнего инвестигейтора — для репортёра и критика, весь тред
    inv_summary: str
    # Интент текущего run'а: {label: simple|complex|continuation, rationale}
    intent: dict
    # Короткий ориентир скаута (его финальное сообщение)
    scout_note: str
    # Вердикт критика текущего run'а: {verdict, feedback}
    critic: dict
    # Сколько раз критик уже отправлял на доработку в этом run'е
    rework: int
    # 1 — объединённое сообщение «интент + скаут» уже дописано в диалог
    intro: int
    # Приватные tool-циклы: AIMessage с tool_calls и ответы ToolMessage
    scout_msgs: Annotated[Sequence[BaseMessage], add_messages]
    inv_msgs: Annotated[Sequence[BaseMessage], add_messages]
    rep_msgs: Annotated[Sequence[BaseMessage], add_messages]
