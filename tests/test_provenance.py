import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from provenance import source_metadata, time_metadata
from reporting import build_investigation_package
from test_investigation_package import state_with_evidence, unzip
from verification_tools import verification_recheck


def test_source_and_time_metadata_come_from_actual_evidence():
    state = state_with_evidence()
    state["evidence"].extend([{
        "id": "source-1",
        "tool": "source_info",
        "status": "ok",
        "result": {
            "source": "earth",
            "endpoint": "https://earth.example",
            "tenant_ids": [2, 3],
            "schema_version": "sha256:abc",
            "schema_fields": 120,
            "fake_earth": False,
        },
    }, {
        "id": "fields-1",
        "tool": "field_lookup",
        "status": "ok",
        "result": {
            "fields": [{
                "field": "original_time",
                "ok": True,
                "type": "DateTime('UTC')",
                "description": "Время события на источнике",
            }],
        },
    }])

    _, url, _ = build_investigation_package("Provenance", "# Вывод", state)
    with unzip(url) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))

    assert manifest["provenance"]["source"] == "earth"
    assert manifest["provenance"]["endpoint"] == "https://earth.example"
    assert manifest["provenance"]["tenant"] == [2, 3]
    assert manifest["provenance"]["schema_version"] == "sha256:abc"
    assert manifest["provenance"]["time_semantics"] == "fields_described_not_normalized"
    assert manifest["provenance"]["time_axes"]["original_time"]["validation"] == "field_lookup"
    assert source_metadata(state)["schema_fields"] == 120
    assert time_metadata(state)[0] == "fields_described_not_normalized"


def test_same_source_recheck_stores_fresh_evidence_and_is_not_independent():
    original = {
        "id": "ev-count-original",
        "tool": "count_events",
        "arguments": {"time_from": "2026-01-01T00:00:00Z"},
        "status": "ok",
        "result": {
            "ok": True,
            "count": 17,
            "window": {"from": "2026-01-01T00:00:00Z", "to": None, "source": "explicit"},
            "duration_s": 1,
        },
    }
    state = {
        "context": {"id": "case-recheck"},
        "evidence": [original],
        "verification_checks": [],
        "mcp_reuse_run_id": "run-recheck",
    }
    runtime = SimpleNamespace(state=state, tool_call_id="call-recheck")

    update = asyncio.run(verification_recheck.coroutine(
        evidence_ids=["ev-count-original"],
        runtime=runtime,
    )).update

    check = update["verification_checks"][0]
    assert check["status"] == "matched"
    assert check["independent"] is False
    assert update["evidence"][0]["id"] == "call-recheck-1"
    assert update["evidence"][0]["result"]["count"] == 17
    assert update["independent_verification"]["status"] == "passed"
    assert update["independent_verification"]["independent"] is False
    assert update["independent_verification"]["method"] == "same_source_replay"


def test_same_source_recheck_marks_changed_data(monkeypatch):
    original = {
        "id": "ev-count-original",
        "tool": "count_events",
        "arguments": {},
        "status": "ok",
        "result": {"ok": True, "count": 17},
    }
    state = {
        "context": {"id": "case-recheck"},
        "evidence": [original],
        "verification_checks": [],
    }

    class ChangedTool:
        async def ainvoke(self, args):
            return ToolMessage(
                content=json.dumps({"ok": True, "count": 18}),
                tool_call_id="fresh",
                name="count_events",
            )

    import tools

    monkeypatch.setitem(tools._MCP_TOOLS_BY_NAME, "count_events", ChangedTool())
    update = asyncio.run(verification_recheck.coroutine(
        evidence_ids=["ev-count-original"],
        runtime=SimpleNamespace(state=state, tool_call_id="call-recheck"),
    )).update

    assert update["verification_checks"][0]["status"] == "changed"
    assert update["independent_verification"]["status"] == "partial"


def test_analysis_sql_recheck_reexecutes_query_and_stores_result(monkeypatch):
    import analysis

    monkeypatch.setattr(analysis, "_artifact", lambda *args: ({"storage": "inline", "key": "analysis"}, ""))

    class Response:
        status_code = 200

        def json(self):
            return {
                "ok": True,
                "columns": ["total"],
                "rows": [{"total": 1}],
                "returned_rows": 1,
                "truncated": False,
                "source_row_count": 1,
            }

    class Client:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(analysis.httpx, "AsyncClient", Client)
    original = {
        "id": "analysis-original",
        "tool": "analysis_sql",
        "arguments": {"dataset_id": "dataset-1", "query": "SELECT COUNT(*) AS total FROM events", "max_rows": 100},
        "status": "ok",
        "result": {
            "ok": True,
            "dataset_id": "dataset-1",
            "dataset_query": {"time_from": "2026-01-01", "time_to": "2026-01-02"},
            "dataset_complete": True,
            "source_evidence_ids": ["ev-page"],
            "run_id": "run-new",
            "columns": ["total"],
            "source_row_count": 1,
            "query": "SELECT COUNT(*) AS total FROM events",
            "max_rows": 100,
            "rows": [{"total": 1}],
            "returned_rows": 1,
            "truncated": False,
            "complete": True,
        },
    }
    dataset = {
        "id": "dataset-1",
        "query": {"time_from": "2026-01-01", "time_to": "2026-01-02"},
        "rows": [{"uuid": "1"}],
        "columns": ["uuid"],
        "complete": True,
        "source_evidence_ids": ["ev-page"],
    }
    state = {
        "context": {"id": "case-analysis-recheck"},
        "evidence": [original],
        "analysis_datasets": [dataset],
        "analysis_results": [],
        "verification_checks": [],
        "mcp_reuse_run_id": "run-new",
    }

    update = asyncio.run(verification_recheck.coroutine(
        evidence_ids=["analysis-original"],
        runtime=SimpleNamespace(state=state, tool_call_id="call-analysis-recheck"),
    )).update

    assert update["verification_checks"][0]["status"] == "matched"
    assert update["independent_verification"]["status"] == "passed"
    assert update["evidence"][0]["tool"] == "analysis_sql"
    assert update["analysis_results"][0]["rows"] == [{"total": 1}]
