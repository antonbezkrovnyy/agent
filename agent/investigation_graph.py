"""Deep Investigation: Planner, item-subgraphs, Item Critic и сборка handoff."""

import asyncio

from langgraph.graph import END, START, StateGraph

from agents import node_investigator
from critics import node_critic_evidence
from planner import item_input, node_collect, node_planner, ready_items
from scope import node_scope
from state import State
from subgraph import apply_delta, invoke_subgraph, state_delta


def _build_item_graph(settings):
    def after_investigator(state: State) -> str:
        return "critic_evidence" if settings.critic_enabled else END

    builder = StateGraph(State)
    builder.add_node("investigator", node_investigator)
    builder.add_node("critic_evidence", node_critic_evidence)
    builder.add_edge(START, "investigator")
    builder.add_conditional_edges("investigator", after_investigator, ["critic_evidence", END])
    return builder.compile()


def build_investigation_graph(settings):
    item_graph = _build_item_graph(settings)

    async def execute(state):
        original = state
        current = state
        while ready_items(current):
            parallel = 1 if current.get("active_skill") else settings.planner_max_parallel
            wave = ready_items(current)[:parallel]
            updates = await asyncio.gather(*(
                invoke_subgraph(item_graph, item_input(current, item))
                for item in wave
            ))
            for update in updates:
                current = apply_delta(current, update)
            current = apply_delta(current, node_collect(current))
            if current.get("replan_requested"):
                break
        return state_delta(original, current)

    def after_execute(state: State) -> str:
        return "planner" if state.get("replan_requested") else END

    builder = StateGraph(State)
    builder.add_node("scope", node_scope)
    builder.add_node("planner", node_planner)
    builder.add_node("execute", execute)
    builder.add_edge(START, "scope")
    builder.add_edge("scope", "planner")
    builder.add_edge("planner", "execute")
    builder.add_conditional_edges("execute", after_execute, ["planner", END])
    return builder.compile()
