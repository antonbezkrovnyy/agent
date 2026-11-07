from langchain_core.messages import AIMessage

from metrics import summarize_agent_run


def test_agent_metrics_count_tokens_tools_and_repeated_queries():
    first = AIMessage(
        content="",
        tool_calls=[
            {"name": "count_events", "args": {"filter": "a"}, "id": "1"},
            {"name": "count_events", "args": {"filter": "a"}, "id": "2"},
            {"name": "timeline", "args": {"interval": "1h"}, "id": "3"},
        ],
        usage_metadata={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
    )
    second = AIMessage(
        content="done",
        response_metadata={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}},
    )

    metrics = summarize_agent_run([first, second], agent="investigator", duration_ms=25)

    assert metrics["tool_calls"] == 3
    assert metrics["tool_calls_by_name"] == {"count_events": 2, "timeline": 1}
    assert metrics["repeated_tool_calls"] == 1
    assert metrics["prompt_tokens"] == 17
    assert metrics["completion_tokens"] == 7
    assert metrics["total_tokens"] == 24
    assert metrics["llm_messages"] == 2
    assert metrics["usage_messages"] == 2
    assert metrics["token_usage_status"] == "available"
    assert metrics["token_usage_complete"] is True
    assert metrics["duration_ms"] == 25


def test_agent_metrics_distinguish_missing_usage_from_zero_consumption():
    metrics = summarize_agent_run([AIMessage(content="done")], agent="investigator")

    assert metrics["llm_messages"] == 1
    assert metrics["usage_messages"] == 0
    assert metrics["total_tokens"] == 0
    assert metrics["token_usage_status"] == "unavailable"
    assert metrics["token_usage_complete"] is False


def test_agent_metrics_read_response_usage_and_mark_partial_coverage():
    with_usage = AIMessage(
        content="first",
        response_metadata={"usage": {"prompt_tokens": 11, "completion_tokens": 5}},
    )
    without_usage = AIMessage(content="second")

    metrics = summarize_agent_run([with_usage, without_usage], agent="investigator")

    assert metrics["llm_messages"] == 2
    assert metrics["usage_messages"] == 1
    assert metrics["total_tokens"] == 16
    assert metrics["token_usage_status"] == "partial"
    assert metrics["token_usage_complete"] is False
