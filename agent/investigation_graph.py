"""Deep Investigation subgraph: investigator и Critic Evidence."""

from langgraph.graph import END, START, StateGraph

from agents import node_investigator
from critics import node_critic_evidence
from state import State


def build_investigation_graph(settings):
    def after_investigator(state: State) -> str:
        return "critic_evidence" if settings.critic_enabled else END

    def after_critic(state: State) -> str:
        critic = state.get("critic") or {}
        if critic.get("verdict") == "rework_data" and state.get("rework", 0) <= settings.max_rework:
            return "investigator"
        return END

    builder = StateGraph(State)
    builder.add_node("investigator", node_investigator)
    builder.add_node("critic_evidence", node_critic_evidence)
    builder.add_edge(START, "investigator")
    builder.add_conditional_edges("investigator", after_investigator, ["critic_evidence", END])
    builder.add_conditional_edges("critic_evidence", after_critic, ["investigator", END])
    return builder.compile()
