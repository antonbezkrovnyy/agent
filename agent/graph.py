"""Граф агента: интент → скаут (параллельно) → агент → критик.

Структура:
- entry — сбрасывает артефакты и служебные каналы при новом сообщении;
- intent и scout — параллельно: интент классифицирует сообщение
  (simple/complex/continuation), скаут делает 1–2 быстрых запроса в MCP
  и кладёт короткий ориентир в диалог;
- dispatch — точка слияния: агенты пускаются только когда готовы обе
  ветки (счётчик ready в стейте; первая прибывшая ветка уходит в
  терминальный no-op wait), затем агент выбирается по интенту и
  контексту стейта (continuation: есть last_report → репортёр, есть
  findings → инвестигейтор, иначе чат);
- chat / investigator / reporter — агенты со своими промптами и тулзами
  (промпты — prompts/*.md, тулзы — tools.py, ноды — agents.py);
- критик — после инвестигейтора (улики) и после репортёра (отчёт);
  вердикт rework отправляет назад на доработку, не больше max_rework раз
  за run (settings.critic_enabled отключает его полностью).

findings и last_report живут весь тред — нужны для правок отчёта
и продолжения расследования.
"""

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.types import Overwrite

from agents import (
    node_chat,
    node_critic_evidence,
    node_critic_report,
    node_investigator,
    node_intent,
    node_reporter,
    node_scout,
)
from config import Settings
from state import State
from tools import investigator_tools, reporter_tools, scout_tools

settings = Settings()


# ── Ноды ─────────────────────────────────────────────────────────

def entry(state: State) -> dict:
    """Сбрасывает артефакты и служебные каналы при новом сообщении.

    Overwrite обходит редьюсер и ставит канал в пустой список —
    файлы прошлых run'ов не прилепляются к новому ответу.
    """
    if state["messages"] and isinstance(state["messages"][-1], HumanMessage):
        return {
            "artifacts": Overwrite([]),
            "intent": {},
            "critic": {},
            "rework": 0,
            # Overwrite обходит редьюсер operator.add и сбрасывает счётчик
            "ready": Overwrite(0),
        }
    return {}


def dispatch(state: State) -> dict:
    """Точка слияния параллельных веток: ждёт, пока готовы и intent, и scout."""
    return {}


def wait(state: State) -> dict:
    """Мертвая ветка: одна из параллельных веток успела раньше,
    вторая ещё в работе — ждём её на dispatch."""
    return {}


# ── Маршрутизация ────────────────────────────────────────────────

def _agent_done(state: State) -> bool:
    """Агент закончил ход: последнее сообщение — ответ без tool_calls."""
    messages = state.get("messages") or []
    return bool(messages) and not getattr(messages[-1], "tool_calls", None)


def route_after_scout(state: State) -> str:
    return "scout_tools" if not _agent_done(state) else "dispatch"


def route_dispatch(state: State) -> str:
    """Агенты пускаются, когда готовы обе ветки (intent + scout).

    Нода с двумя входящими ребрами активизируется по первому прибытию
    обновления, а не по последнему (граф-api: «a node becomes active
    when it receives a new message on any of its incoming edges»),
    поэтому пока ready < 2 уводим в терминальный no-op wait.
    """
    if state.get("ready", 0) < 2:
        return "wait"
    return dispatch_agent(state)


def dispatch_agent(state: State) -> str:
    """Выбор агента: по интенту; continuation — по контексту стейта."""
    label = (state.get("intent") or {}).get("label") or "simple"
    if label == "complex":
        return "investigator"
    if label == "continuation":
        if state.get("last_report"):
            return "reporter"
        if state.get("findings"):
            return "investigator"
        return "chat"
    return "chat"


def route_after_investigator(state: State) -> str:
    if not _agent_done(state):
        return "investigator_tools"
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
    if not _agent_done(state):
        return "reporter_tools"
    # Критик отчёта — только если репортёр реально отчёт создал
    if settings.critic_enabled and state.get("artifacts"):
        return "critic_report"
    return END


def route_after_critic_report(state: State) -> str:
    critic = state.get("critic") or {}
    if (
        critic.get("verdict") == "rework_report"
        and state.get("rework", 0) <= settings.max_rework
    ):
        return "reporter"
    return END


# ── Сборка ───────────────────────────────────────────────────────

def build_graph(checkpointer=None):
    """checkpointer — опционально: в agra персистентность своя,
    в локальных тестах передаётся InMemorySaver."""
    builder = StateGraph(State)

    builder.add_node("entry", entry)
    builder.add_node("intent", node_intent)
    builder.add_node("scout", node_scout)
    builder.add_node("scout_tools", ToolNode(scout_tools))
    builder.add_node("dispatch", dispatch)
    builder.add_node("wait", wait)
    builder.add_node("chat", node_chat)
    builder.add_node("investigator", node_investigator)
    builder.add_node("investigator_tools", ToolNode(investigator_tools))
    builder.add_node("reporter", node_reporter)
    builder.add_node("reporter_tools", ToolNode(reporter_tools))
    builder.add_node("critic_evidence", node_critic_evidence)
    builder.add_node("critic_report", node_critic_report)

    builder.add_edge(START, "entry")
    # Параллельные ветки: интент и скаут стартуют одновременно
    builder.add_edge("entry", "intent")
    builder.add_edge("entry", "scout")
    builder.add_edge("intent", "dispatch")
    builder.add_conditional_edges("scout", route_after_scout, ["scout_tools", "dispatch"])
    builder.add_edge("scout_tools", "scout")

    builder.add_conditional_edges(
        "dispatch", route_dispatch, ["chat", "investigator", "reporter", "wait"],
    )
    builder.add_edge("chat", END)

    builder.add_conditional_edges(
        "investigator",
        route_after_investigator,
        ["investigator_tools", "critic_evidence", "reporter"],
    )
    builder.add_edge("investigator_tools", "investigator")
    builder.add_conditional_edges(
        "critic_evidence", route_after_critic_evidence, ["investigator", "reporter"],
    )

    builder.add_conditional_edges(
        "reporter", route_after_reporter, ["reporter_tools", "critic_report", END],
    )
    builder.add_edge("reporter_tools", "reporter")
    builder.add_conditional_edges("critic_report", route_after_critic_report, ["reporter", END])

    return builder.compile(checkpointer=checkpointer)


graph = build_graph()
