"""Второй агент: тот же LLM, те же тулы и логика отчётов, что в graph.py,
но граф собран через create_agent + middleware вместо ручного StateGraph.

Разница с graph.py — только способ сборки:
- цикл llm → tools → llm и роутинг (tools_condition) — делает create_agent
- system prompt — параметр system_prompt
- reports/callback_url — состояние в subclass AgentState (state_schema)
- сброс отчётов в начале запуска — хук before_agent
- пришивка отчётов (artifacts) к финальному сообщению — хук after_model

A/B: оба агента зарегистрированы в agra.json, сравниваются в UI.
"""

from typing import Annotated

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from graph import SYSTEM_PROMPT, REPORTS_CLEAR, _merge_reports, llm, tools


class Agent2State(AgentState):
    reports: Annotated[list, _merge_reports]
    callback_url: str


class ReportMiddleware(AgentMiddleware):
    """Отчёты: сброс в начале запуска, пришивка к финальному сообщению."""

    def before_agent(self, state, runtime):
        return {"reports": [REPORTS_CLEAR]}

    def after_model(self, state, runtime):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and not getattr(last, "tool_calls", None):
            pending = state.get("reports") or []
            if pending:
                last.artifacts = list(pending)
                return {"reports": [REPORTS_CLEAR]}
        return None


graph = create_agent(
    llm,
    tools,
    system_prompt=SYSTEM_PROMPT,
    state_schema=Agent2State,
    middleware=[ReportMiddleware()],
)
