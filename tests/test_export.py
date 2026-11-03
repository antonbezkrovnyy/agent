from conftest import FakeAgent, FakeStructured, ai, build_graph, intent, invoke, tc
from langchain_core.messages import ToolMessage


def test_reporter_uses_extract_data_once(patch_llm):
    agent = FakeAgent({
        "Ты — скаут": [ai(calls=[tc("count_events", {})]), ai("17 событий")],
        "Ты — инвестигейтор": [ai("17 событий")],
        "Ты — репортёр": [
            ai(calls=[tc("extract_data", {
                "time_from": "2026-01-01T00:00:00Z",
                "time_to": "2026-01-01T01:00:00Z",
                "columns": ["uuid", "timestamp", "message"],
            })]),
            ai("Вот полная таблица"),
        ],
    })
    patch_llm(agent, FakeStructured(intent()))

    result = invoke(build_graph(), "пришли все события таблицей")

    exports = [m for m in result["rep_msgs"] if isinstance(m, ToolMessage) and m.name == "extract_data"]
    assert len(exports) == 1
    assert result["report_result"]["answer"] == "Вот полная таблица"
    evidence = next(e for e in result["evidence"] if e["tool"] == "extract_data")
    assert evidence["arguments"]["time_from"] == "2026-01-01T00:00:00Z"
    assert evidence["arguments"]["time_to"] == "2026-01-01T01:00:00Z"
    assert isinstance(exports[0].content, str)


def test_full_table_uses_deterministic_export_without_llm(patch_llm):
    from llm import IntentDecision

    agent = FakeAgent({})
    decision = IntentDecision(
        intent="continuation",
        rationale="нужна полная таблица текущего фильтра",
        export_data=True,
    )
    patch_llm(agent, FakeStructured(decision))
    context = {
        "id": "investigation-1",
        "time_from": "2026-10-31T07:46:55Z",
        "time_to": "2026-10-31T08:46:55Z",
        "timezone": "UTC",
    }
    previous = {
        "id": "previous-export",
        "tool": "extract_data",
        "arguments": {
            "filter": {"contains": [{"var": "message"}, "ping"]},
            "columns": ["uuid", "message"],
            "max_rows": 10,
        },
        "result": {"returned_rows": 10, "complete": False},
    }

    result = invoke(
        build_graph(),
        "пришли все пинги таблицей",
        thread_id="deterministic-export",
        context=context,
        evidence=[previous],
    )

    assert agent.calls == 0
    export = result["evidence"][-1]
    assert export["tool"] == "extract_data"
    assert export["arguments"]["filter"] == previous["arguments"]["filter"]
    assert "max_rows" not in export["arguments"]
    assert result["context"]["id"] == context["id"]
    assert "| uuid | message |" in result["report_result"]["answer"]
    assert "| event-42 | скачок CPU |" in result["report_result"]["answer"]
    assert result["progress"]["state"] == "done"
    assert result["suggestions"][0]["label"] == "Продолжить расследование"
    assert result["artifacts"]
