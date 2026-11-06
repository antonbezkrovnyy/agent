"""Reporting subgraph: reporter и Critic Report; rework_data возвращается supervisor."""

from langgraph.graph import END, START, StateGraph

from agents import node_reporter
from critics import node_critic_report
from state import State


def build_reporting_graph(settings):
    def after_reporter(state: State) -> str:
        verification = (state.get("report_result") or {}).get("verification") or {}
        if verification.get("status") == "update_failed":
            return END
        if settings.critic_enabled and state.get("report_result"):
            return "critic_report"
        return END

    def after_critic(state: State) -> str:
        critic = state.get("critic") or {}
        if critic.get("verdict") == "rework_report" and state.get("rework", 0) <= settings.max_rework:
            return "reporter"
        return END

    builder = StateGraph(State)
    builder.add_node("reporter", node_reporter)
    builder.add_node("critic_report", node_critic_report)
    builder.add_edge(START, "reporter")
    builder.add_conditional_edges("reporter", after_reporter, ["critic_report", END])
    builder.add_conditional_edges("critic_report", after_critic, ["reporter", END])
    return builder.compile()
