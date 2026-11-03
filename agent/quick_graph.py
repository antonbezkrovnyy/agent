"""Quick Investigation subgraph: одна ограниченная проверка scout."""

from langgraph.graph import END, START, StateGraph

from agents import node_scout
from state import State


def build_quick_graph():
    builder = StateGraph(State)
    builder.add_node("scout", node_scout)
    builder.add_edge(START, "scout")
    builder.add_edge("scout", END)
    return builder.compile()
