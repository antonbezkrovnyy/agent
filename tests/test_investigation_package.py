import base64
import csv
import io
import json
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

from evidence import chain_markdown, queries_markdown
from reporting import build_investigation_package
from tools import save_report


def test_manifest_serializes_datetime_as_iso():
    state = state_with_evidence()
    moment = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)
    state["run_metrics"] = [{"started_at": moment}]

    _, url, _ = build_investigation_package("Проверка", "# Вывод", state)
    manifest = json.loads(unzip(url).read("manifest.json"))

    assert manifest["run_metrics"][0]["started_at"] == moment.isoformat()


def state_with_evidence():
    return {
        "context": {"id": "case-package"},
        "messages": [SimpleNamespace(type="human", content="Проверь allow"),
        ],
        "work_mode": "quick",
        "depth_history": [],
        "findings": [],
        "hypotheses": [],
        "skill_runs": [],
        "anchors": [],
        "visited_anchors": [],
        "inv_result": {},
        "report_result": {},
        "evidence": [
            {
                "id": "ev-search",
                "investigation_id": "case-package",
                "step_id": "ev-search",
                "tool": "search_events",
                "arguments": {"filter": {"==": [{"var": "action"}, "allow"]}},
                "iql": "action = 'allow'",
                "window": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T01:00:00Z"},
                "status": "ok",
                "complete": True,
                "result_ref": "evidence://case-package/ev-search",
                "created_at": "2026-01-01T00:10:00Z",
                "result": {
                    "ok": True,
                    "events": [
                        {"uuid": "event-1", "action": "allow", "message": "allowed"},
                        {"uuid": "event-2", "action": "allow", "message": "allowed again"},
                    ],
                },
                "error": None,
            },
            {
                "id": "ev-count",
                "investigation_id": "case-package",
                "step_id": "ev-count",
                "tool": "count_events",
                "arguments": {"time_from": "2026-01-01T00:00:00Z"},
                "iql": None,
                "window": {"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T01:00:00Z"},
                "status": "ok",
                "complete": True,
                "result_ref": "evidence://case-package/ev-count",
                "created_at": "2026-01-01T00:11:00Z",
                "result": {"ok": True, "count": 2},
                "error": None,
            },
        ],
    }


def unzip(url):
    encoded = url.split("base64,", 1)[1].split("#", 1)[0]
    return zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded)))


def test_package_contains_queries_evidence_events_and_manifest():
    state = state_with_evidence()

    _, url, _ = build_investigation_package("Проверка", "# Вывод", state)
    bundle = unzip(url)

    assert set(bundle.namelist()) == {
        "report.md",
        "queries.md",
        "evidence.jsonl",
        "events.csv",
        "manifest.json",
    }
    queries = bundle.read("queries.md").decode()
    assert "action = 'allow'" in queries
    assert '"time_from": "2026-01-01T00:00:00Z"' in queries

    evidence_lines = bundle.read("evidence.jsonl").decode().strip().splitlines()
    assert len(evidence_lines) == 2
    assert json.loads(evidence_lines[0])["iql"] == "action = 'allow'"
    assert json.loads(evidence_lines[0])["result"]["events"][0]["uuid"] == "event-1"

    events = list(csv.DictReader(io.StringIO(bundle.read("events.csv").decode())))
    assert len(events) == 2
    assert events[0]["_evidence_id"] == "ev-search"
    assert events[0]["uuid"] == "event-1"

    manifest = json.loads(bundle.read("manifest.json"))
    assert manifest["investigation_id"] == "case-package"
    assert manifest["evidence_count"] == 2
    assert manifest["events_count"] == 2
    assert manifest["evidence_index"] == {"ev-search": 1, "ev-count": 2}
    assert len(manifest["files"]) == 4
    assert all(item["sha256"] for item in manifest["files"])


def test_chain_links_query_to_jsonl_record():
    state = state_with_evidence()

    report = chain_markdown(state)

    assert "Результат: `evidence.jsonl`, запись 1." in report
    assert "```iql\naction = 'allow'\n```" in report
    assert "```json" in report
    assert "Запросы расследования" in queries_markdown(state)


def test_save_report_attaches_markdown_and_package():
    state = state_with_evidence()
    runtime = SimpleNamespace(state=state, tool_call_id="call-report")

    result = save_report.func("Проверка", "# Вывод", runtime=runtime)

    assert len(result.update["artifacts"]) == 2
    assert result.update["artifacts"][0]["content_type"] == "text/markdown"
    assert result.update["artifacts"][1]["content_type"] == "application/zip"
    assert result.update["artifacts"][0]["key"].startswith("investigations/case-package/")
    assert result.update["last_report"]["package_filename"].endswith(".zip")
    payload = json.loads(result.update["messages"][0].content)
    assert payload["package_bytes"] > 0
