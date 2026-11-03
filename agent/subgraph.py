"""Вызов субграфа и преобразование полного результата в delta supervisor."""

from langchain_core.messages import RemoveMessage


_APPEND_KEYS = ("artifacts", "evidence", "findings", "skill_runs")
_MESSAGE_KEYS = ("messages", "scout_msgs", "inv_msgs", "rep_msgs")


def _message_delta(before, after):
    before_by_id = {item.id: item for item in before}
    after_ids = {item.id for item in after}
    removed = [RemoveMessage(id=item_id) for item_id in before_by_id if item_id not in after_ids]
    added = [item for item in after if item.id not in before_by_id]
    return [*removed, *added]


async def invoke_subgraph(graph, state):
    result = await graph.ainvoke(state)
    update = {}
    for key, value in result.items():
        if key in _APPEND_KEYS:
            before = len(state.get(key) or [])
            if len(value or []) > before:
                update[key] = value[before:]
        elif key in _MESSAGE_KEYS:
            delta = _message_delta(state.get(key) or [], value or [])
            if delta:
                update[key] = delta
        elif value != state.get(key):
            update[key] = value
    return update
