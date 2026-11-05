"""Run-scoped ручные переопределения prompt для управляемого режима."""

from langchain_core.messages import SystemMessage


def effective_prompt(state: dict, role: str, fallback: str) -> str:
    override = (state.get("control_overrides") or {}).get(role) or {}
    return str(override.get("prompt") or fallback)


def with_prompt_override(state: dict, role: str, messages: list) -> list:
    if not messages:
        return messages
    fallback = messages[0].content if isinstance(messages[0], SystemMessage) else ""
    prompt = effective_prompt(state, role, fallback)
    if prompt == fallback:
        return messages
    return [SystemMessage(content=prompt), *messages[1:]]
