import asyncio
import json
from types import SimpleNamespace

import analysis
from reporting import build_investigation_package
from test_investigation_package import unzip


def runtime(state, call_id):
    return SimpleNamespace(state=state, tool_call_id=call_id)


def fake_artifact(filename, data, content_type, context, run_id):
    return {
        "storage": "s3",
        "key": f"investigations/case-analysis/current/{filename}",
        "filename": filename,
        "content_type": content_type,
        "bytes": len(data),
    }, ""


def test_dataset_sql_and_package_are_self_contained(monkeypatch):
    monkeypatch.setattr(analysis, "_artifact", fake_artifact)
    state = {
        "context": {
            "id": "case-analysis",
            "time_from": "2026-11-06T05:00:00Z",
            "time_to": "2026-11-06T05:30:00Z",
        },
        "mcp_reuse_run_id": "run-analysis",
        "analysis_datasets": [],
    }

    dataset_update = asyncio.run(analysis.analysis_dataset_create.coroutine(
        iql="action = 'deny'",
        columns=["uuid", "message"],
        runtime=runtime(state, "call-dataset"),
    )).update

    dataset = dataset_update["analysis_datasets"][0]
    assert dataset["row_count"] == 1
    assert dataset["complete"] is True
    assert dataset["query"]["iql"] == "action = 'deny'"
    assert dataset_update["evidence"][0]["tool"] == "field_lookup"
    extract_evidence = dataset_update["evidence"][1]
    assert extract_evidence["tool"] == "extract_data"
    assert extract_evidence["parent_call_id"] == "call-dataset"
    assert extract_evidence["result"]["events"][0]["uuid"] == "event-42"
    assert dataset_update["artifacts"][0]["filename"].endswith(".jsonl")

    class Response:
        status_code = 200

        def json(self):
            return {
                "ok": True,
                "columns": ["total", "unique_pairs"],
                "rows": [{"total": 989, "unique_pairs": 989}],
                "returned_rows": 1,
                "truncated": False,
                "duration_ms": 3,
            }

    class Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            assert json["dataset_id"] == dataset["id"]
            assert json["rows"] == dataset["rows"]
            return Response()

    monkeypatch.setattr(analysis.httpx, "AsyncClient", Client)
    state["analysis_datasets"] = [dataset]
    sql_update = asyncio.run(analysis.analysis_sql.coroutine(
        dataset_id=dataset["id"],
        query="SELECT COUNT(*) AS total, COUNT(DISTINCT (src_ip, dst_ip)) AS unique_pairs FROM events",
        runtime=runtime(state, "call-sql"),
    )).update

    evidence = sql_update["evidence"][0]
    assert evidence["tool"] == "analysis_sql"
    assert evidence["status"] == "ok"
    assert evidence["result"]["rows"][0]["unique_pairs"] == 989
    assert evidence["result"]["source_evidence_ids"] == [item["id"] for item in dataset_update["evidence"]]
    assert sql_update["analysis_results"][0]["complete"] is True

    package_state = {
        "context": state["context"],
        "evidence": [*dataset_update["evidence"], evidence],
        "analysis_datasets": [dataset],
        "analysis_results": sql_update["analysis_results"],
        "tool_calls": [],
    }
    _, url, _ = build_investigation_package("SQL", "# Вывод", package_state)
    with unzip(url) as bundle:
        names = bundle.namelist()
        dataset_path = next(name for name in names if name.startswith("datasets/"))
        result_path = next(name for name in names if name.startswith("analysis_results/"))
        rows = [json.loads(line) for line in bundle.read(dataset_path).decode().splitlines()]
        result = json.loads(bundle.read(result_path))
        manifest = json.loads(bundle.read("manifest.json"))

    assert rows[0]["uuid"] == "event-42"
    assert result["rows"][0]["total"] == 989
    assert manifest["analysis_datasets"][0]["complete"] is True
    assert manifest["analysis_results"][0]["query"].startswith("SELECT COUNT")


def test_analysis_sql_marks_truncated_or_partial_dataset_as_partial(monkeypatch):
    monkeypatch.setattr(analysis, "_artifact", fake_artifact)
    dataset = {
        "id": "dataset-partial",
        "query": {"time_from": "2026-01-01", "time_to": "2026-01-02"},
        "rows": [{"uuid": "1"}],
        "complete": False,
        "source_evidence_ids": ["ev-page"],
    }
    state = {
        "context": {"id": "case-analysis"},
        "analysis_datasets": [dataset],
    }

    class Response:
        status_code = 200

        def json(self):
            return {"ok": True, "rows": [], "returned_rows": 0, "truncated": False}

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
    update = asyncio.run(analysis.analysis_sql.coroutine(
        dataset_id="dataset-partial",
        query="SELECT * FROM events",
        runtime=runtime(state, "call-partial"),
    )).update

    assert update["evidence"][0]["status"] == "partial"
    assert update["evidence"][0]["complete"] is False


def test_identical_dataset_and_sql_are_reused(monkeypatch):
    monkeypatch.setattr(analysis, "_artifact", fake_artifact)
    state = {
        "context": {
            "id": "case-reuse",
            "time_from": "2026-11-06T05:00:00Z",
            "time_to": "2026-11-06T05:30:00Z",
        },
        "mcp_reuse_run_id": "run-analysis",
        "analysis_datasets": [],
        "analysis_results": [],
    }
    runtime_state = runtime(state, "call-dataset-first")
    first = asyncio.run(analysis.analysis_dataset_create.coroutine(runtime=runtime_state)).update
    dataset = first["analysis_datasets"][0]
    assert "src_ip" in dataset["query"]["columns"]
    assert "dst_ip" in dataset["query"]["columns"]
    assert "original_time" in dataset["query"]["columns"]
    state["analysis_datasets"] = [dataset]

    second = asyncio.run(analysis.analysis_dataset_create.coroutine(
        runtime=runtime(state, "call-dataset-second"),
    )).update
    assert "analysis_datasets" not in second
    assert "evidence" not in second
    reused = json.loads(second["messages"][0].content)
    assert reused["reused"] is True
    assert reused["dataset_id"] == dataset["id"]

    query = "SELECT COUNT(*) AS total FROM events"
    state["analysis_results"] = [{
        "id": "sql-first",
        "dataset_id": dataset["id"],
        "query": query,
        "max_rows": 10000,
        "run_id": "run-analysis",
        "complete": True,
        "rows": [{"total": 1}],
    }]
    third = asyncio.run(analysis.analysis_sql.coroutine(
        dataset_id=dataset["id"],
        query=query,
        runtime=runtime(state, "call-sql-second"),
    )).update
    assert "evidence" not in third
    reused_sql = json.loads(third["messages"][0].content)
    assert reused_sql["reused"] is True
    assert reused_sql["evidence_id"] == "sql-first"


def test_unavailable_schema_columns_are_explicit_and_make_dataset_partial(monkeypatch):
    monkeypatch.setattr(analysis, "_artifact", fake_artifact)

    class PartialLookup:
        async def ainvoke(self, args):
            from langchain_core.messages import ToolMessage

            return ToolMessage(
                content=json.dumps({
                    "ok": True,
                    "complete": False,
                    "fields": [
                        {"field": name, "ok": name != "original_time", "type": "String"}
                        for name in args["fields"]
                    ],
                }),
                tool_call_id="lookup-partial",
                name="field_lookup",
            )

    import tools

    monkeypatch.setitem(tools._MCP_TOOLS_BY_NAME, "field_lookup", PartialLookup())
    state = {
        "context": {
            "id": "case-unavailable",
            "time_from": "2026-11-06T05:00:00Z",
            "time_to": "2026-11-06T05:30:00Z",
        },
        "mcp_reuse_run_id": "run-unavailable",
        "analysis_datasets": [],
    }

    update = asyncio.run(analysis.analysis_dataset_create.coroutine(
        runtime=runtime(state, "call-unavailable"),
    )).update

    dataset = update["analysis_datasets"][0]
    assert dataset["complete"] is False
    assert dataset["unavailable_columns"] == ["original_time"]
    assert "original_time" not in dataset["query"]["columns"]
    assert dataset["requested_columns"] == list(analysis._DEFAULT_ANALYSIS_COLUMNS)
    assert update["evidence"][0]["tool"] == "field_lookup"
    assert update["evidence"][0]["status"] == "partial"


def test_analysis_sql_allows_six_attempts_and_stops_seventh(monkeypatch):
    monkeypatch.setattr(analysis, "_artifact", fake_artifact)
    dataset = {
        "id": "dataset-attempts",
        "query": {"time_from": "2026-01-01", "time_to": "2026-01-02"},
        "rows": [{"uuid": "1"}],
        "columns": ["uuid"],
        "complete": True,
        "source_evidence_ids": [],
    }
    state = {
        "context": {"id": "case-attempts"},
        "analysis_datasets": [dataset],
        "analysis_results": [],
        "mcp_reuse_run_id": "run-attempts",
    }

    class Response:
        status_code = 200

        def json(self):
            return {"ok": False, "error": "invalid test SQL", "error_code": "invalid_literal"}

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
    for attempt in range(1, 7):
        update = asyncio.run(analysis.analysis_sql.coroutine(
            dataset_id=dataset["id"],
            query=f"SELECT {attempt} AS attempt",
            runtime=runtime(state, f"call-attempt-{attempt}"),
        )).update
        state["analysis_results"].extend(update["analysis_results"])
        assert update["analysis_results"][0]["attempt"] == attempt
        assert update["analysis_results"][0]["max_attempts"] == 6

    update = asyncio.run(analysis.analysis_sql.coroutine(
        dataset_id=dataset["id"],
        query="SELECT 7 AS attempt",
        runtime=runtime(state, "call-attempt-7"),
    )).update
    result = json.loads(update["messages"][0].content)

    assert result["error_code"] == "sql_attempt_limit"
    assert result["attempt"] == 7
    assert result["max_attempts"] == 6
    assert "evidence" not in update
