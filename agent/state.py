"""Каналы общего State.

Только данные — поведение описано в agents.py и graph.py.
"""

import operator
from typing import Annotated, Sequence

from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class State(TypedDict):
    # Диалог: сообщения пользователя, ответы агентов, tool-сообщения
    messages: Annotated[Sequence[BaseMessage], add_messages]
    # Эндпоинт для доставки отчёта (из данных запуска)
    callback_url: str
    # Артефакты (data-URL отчётов) текущего run'а; сбрасываются в entry
    artifacts: Annotated[list[str], operator.add]
    # Записи расследования (log_finding), живут весь тред
    findings: Annotated[list[str], operator.add]
    # Последний отчёт {title, markdown, filename} — для правок, живёт весь тред
    last_report: dict
    # Интент текущего run'а: {label: simple|complex|continuation, rationale}
    intent: dict
    # Вердикт критика текущего run'а: {verdict, feedback}
    critic: dict
    # Сколько раз критик уже отправлял на доработку в этом run'е
    rework: int
    # Счётчик готовых параллельных веток (intent + scout) — барьер для выбора агента
    ready: Annotated[int, operator.add]
