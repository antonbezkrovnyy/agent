"""Вызов субграфа и преобразование полного результата в delta supervisor."""

from langchain_core.messages import RemoveMessage
from langgraph.graph.message import add_messages

from state import merge_hypotheses, merge_progress, merge_tool_usage, merge_work_items


_APPEND_KEYS = ("artifacts", "evidence", "findings", "skill_runs", "memory_operations", "run_metrics")
_MESSAGE_KEYS = ("messages", "scout_msgs", "inv_msgs", "rep_msgs")


def _message_delta(before, after):
    before_by_id = {item.id: item for item in before}
    after_ids = {item.id for item in after}
    removed = [RemoveMessage(id=item_id) for item_id in before_by_id if item_id not in after_ids]
    added = [item for item in after if item.id not in before_by_id]
    return [*removed, *added]


def state_delta(before: dict, after: dict) -> dict:
    update = {}
    for key, value in after.items():
        if key in _APPEND_KEYS:
            previous = len(before.get(key) or [])
            if len(value or []) > previous:
                update[key] = value[previous:]
        elif key in _MESSAGE_KEYS:
            delta = _message_delta(before.get(key) or [], value or [])
            if delta:
                update[key] = delta
        elif value != before.get(key):
            update[key] = value
    return update


def apply_delta(state: dict, update: dict) -> dict:
    result = dict(state)
    for key, value in update.items():
        if key in _APPEND_KEYS:
            result[key] = [*(state.get(key) or []), *value]
        elif key in _MESSAGE_KEYS:
            result[key] = add_messages(state.get(key) or [], value)
        elif key == "work_items":
            active_id = (update.get("active_work_item") or {}).get("id")
            changed = [item for item in value if item.get("id") == active_id] if active_id else value
            result[key] = merge_work_items(state.get(key) or [], changed)
        elif key == "hypotheses":
            result[key] = merge_hypotheses(state.get(key) or [], value)
        elif key == "tool_usage":
            result[key] = merge_tool_usage(state.get(key) or {}, value)
        elif key in ("progress", "active_work_item"):
            result[key] = merge_progress(state.get(key) or {}, value)
        else:
            result[key] = value
    return result


async def invoke_subgraph(graph, state):
    result = await graph.ainvoke(state)
    return state_delta(state, result)
