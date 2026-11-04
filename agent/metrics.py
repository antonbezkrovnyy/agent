"""Сравнимые технические метрики одного вызова agent-роли."""

import json
from collections import Counter


def _tokens(message) -> tuple[int, int]:
    usage = getattr(message, "usage_metadata", None) or {}
    if not usage:
        usage = (getattr(message, "response_metadata", None) or {}).get("token_usage") or {}
    prompt = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    completion = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return int(prompt), int(completion)


def summarize_agent_run(messages: list, **fields) -> dict:
    tools = Counter()
    signatures = Counter()
    prompt_tokens = 0
    completion_tokens = 0
    llm_messages = 0
    for message in messages:
        prompt, completion = _tokens(message)
        if prompt or completion:
            llm_messages += 1
        prompt_tokens += prompt
        completion_tokens += completion
        for call in getattr(message, "tool_calls", None) or []:
            name = str(call.get("name") or "unknown")
            tools[name] += 1
            signature = json.dumps(
                {"name": name, "args": call.get("args") or {}},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            signatures[signature] += 1
    return {
        **fields,
        "llm_messages": llm_messages,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "tool_calls": sum(tools.values()),
        "tool_calls_by_name": dict(tools),
        "repeated_tool_calls": sum(count - 1 for count in signatures.values() if count > 1),
    }
