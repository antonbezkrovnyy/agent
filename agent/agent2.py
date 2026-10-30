"""Второй агент: тот же LLM, те же тулы и логика отчётов, что в graph.py,
но граф собран через create_agent + middleware вместо ручного StateGraph.

Разница с graph.py — только способ сборки:
- цикл llm → tools → llm и роутинг (tools_condition) — делает create_agent
- system prompt — параметр system_prompt
- callback_url — состояние в subclass AgentState (state_schema)
- пришивка отчётов (artifacts) к финальному сообщению — хук after_model

A/B: оба агента зарегистрированы в agra.json, сравниваются в UI.
"""

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from graph import SYSTEM_PROMPT, _pending_report_artifacts, llm, tools


class Agent2State(AgentState):
    callback_url: str


class ReportMiddleware(AgentMiddleware):
    """Отчёты: data-URL из artifact ToolMessage — в artifacts финального сообщения."""

    def after_model(self, state, runtime):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and not getattr(last, "tool_calls", None):
            pending = _pending_report_artifacts(state["messages"][:-1])
            if pending:
                last.artifacts = list(pending)
        return None


graph = create_agent(
    llm,
    tools,
    system_prompt=SYSTEM_PROMPT,
    state_schema=Agent2State,
    middleware=[ReportMiddleware()],
)
