from datetime import datetime

from conftest import (
    FakeAgent,
    FakeStructured,
    ai,
    build_graph,
    intent,
    invoke,
    tc,
)
from langchain_core.messages import ToolMessage


def test_evidence_is_structured_and_window_is_frozen(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("server_time", {})]), ai("Есть события")],
        "Ты — инвестигейтор": [
            ai(calls=[tc("count_events", {})]),
            ai(calls=[tc("log_finding", {
                "claim": "17 событий", "evidence_ids": [], "next_action": "готово",
            })]),
            ai("За час 17 событий"),
        ],
        "Ты — репортёр": [ai("За час 17 событий")],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "что в логикоре за последний час")
    context = result["context"]

    assert len(result["evidence"]) == 2
    assert result["findings"][0]["claim"] == "17 событий"
    finding_messages = [message for message in result["messages"] if message.name == "finding"]
    assert len(finding_messages) == 1
    assert "17 событий" in finding_messages[0].content
    assert not finding_messages[0].tool_calls
    delta = datetime.fromisoformat(context["time_to"]) - datetime.fromisoformat(context["time_from"])
    assert delta.total_seconds() == 3600
    for evidence in result["evidence"]:
        assert set(evidence) >= {"id", "tool", "arguments", "window", "status", "result"}
        if evidence["tool"] == "count_events":
            assert evidence["arguments"]["time_from"] == context["time_from"]
            assert evidence["arguments"]["time_to"] == context["time_to"]
            assert evidence["result"]["count"] == 17
    for channel in ("scout_msgs", "inv_msgs"):
        for message in result[channel]:
            if isinstance(message, ToolMessage):
                assert isinstance(message.content, str)
                assert "[object Object]" not in message.content


def test_tool_exception_becomes_error_evidence(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("Есть события")],
        "Ты — инвестигейтор": [
            ai(calls=[tc("broken_tool", {"time_from": "bad"})]),
            ai("Не удалось выполнить запрос"),
        ],
        "Ты — репортёр": [ai("Данные недоступны")],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "проверь события")
    errors = [item for item in result["evidence"] if item["status"] == "error"]

    assert len(errors) == 1
    assert errors[0]["tool"] == "broken_tool"
    assert errors[0]["arguments"]["time_from"] == "bad"
    assert errors[0]["arguments"]["time_to"] == result["context"]["time_to"]
    assert "ValueError" in errors[0]["error"]


def test_log_finding_rejects_unknown_evidence_id(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("Есть события")],
        "Ты — инвестигейтор": [
            ai(calls=[tc("log_finding", {
                "claim": "Недоказанное", "evidence_ids": ["missing"], "next_action": "стоп",
            })]),
            ai("Доказательств нет"),
        ],
        "Ты — репортёр": [ai("Доказательств нет")],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "проверь события")

    assert result["findings"] == []
    errors = [m for m in result["inv_msgs"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(errors) == 1
    assert "missing" in errors[0].content


def test_tool_result_updates_nested_progress(patch_llm):
    import asyncio

    from conftest import agents_mod

    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("server_time", {}), tc("server_time", {})]), ai("Готово")],
    })
    patch_llm(agent, FakeStructured(intent()))
    state = {
        "messages": [],
        "callback_url": "",
        "artifacts": [],
        "findings": [],
        "evidence": [],
        "context": {},
        "last_report": {},
        "inv_summary": "",
        "inv_result": {},
        "scout_note": "",
        "scout_result": {},
        "critic": {},
        "rework": 0,
        "tool_usage": {},
        "progress": {},
    }

    result, _, _ = asyncio.run(agents_mod._run_agent("scout", state, "scout_msgs"))

    assert result["progress"]["agent"] == "scout"
    assert result["progress"]["tool_calls"] == 2
    assert result["progress"]["tool_limit"] == 10
