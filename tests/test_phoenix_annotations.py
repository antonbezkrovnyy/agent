import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("AGENT_MEMORY_DATABASE_URL", "postgresql://test/test")
sys.path.insert(0, str(Path(__file__).parents[1] / "agent-evaluator"))

import app as evaluator_app
from models import EvaluationRecord
from phoenix_client import PhoenixClient, run_summary_annotations


class Traces:
    def __init__(self):
        self.calls = []

    def add_trace_annotation(self, **kwargs):
        self.calls.append(kwargs)


class SDK:
    def __init__(self):
        self.traces = Traces()


def _deep_state() -> dict:
    return {
        "intent": {"intent": "complex", "work_mode": "deep", "rationale": "нужны причины спада"},
        "work_mode": "deep",
        "investigation_scope": {"mode": "discovery", "max_items": 2},
        "investigation_plan": {"version": 2, "items": [
            {"id": "item-001", "status": "done"},
            {"id": "item-002", "status": "done"},
        ]},
        "evidence": [{"id": "e1", "status": "ok"}, {"id": "e2", "status": "ok"}],
        "report_result": {"claims": [
            {"text": "c1", "evidence_ids": ["e1"]},
            {"text": "c2", "evidence_ids": ["e2"]},
        ]},
        "hypotheses": [{"status": "supported"}, {"status": "rejected"}, {"status": "open"}],
        "critic": {"verdict": "ok", "issues": []},
    }


def test_evaluation_is_written_as_phoenix_trace_annotation():
    client = PhoenixClient("http://phoenix:6006", "logicore-agent")
    client._sdk = SDK()
    record = EvaluationRecord(
        run_id="run-1",
        thread_id="thread-1",
        trace_id="trace-1",
        verdict="warn",
        score=0.7,
        summary="Есть замечание",
        model="evaluator-model",
    )

    client.annotate(record)

    call = client._sdk.traces.calls[0]
    assert call["trace_id"] == "trace-1"
    assert call["annotation_name"] == "logicore_agent_quality"
    assert call["annotator_kind"] == "LLM"
    assert call["label"] == "warn"
    assert call["metadata"]["run_id"] == "run-1"
    assert call["sync"] is True


def test_run_summary_annotations_classify_deep_run():
    annotations = {item["name"]: item for item in run_summary_annotations(_deep_state())}

    assert annotations["run_type"]["label"] == "deep"
    assert annotations["scope_mode"]["label"] == "discovery"
    assert annotations["plan_execution"]["score"] == 1.0
    assert annotations["plan_execution"]["explanation"] == "items 2/2 done; replans=1"
    assert annotations["claims_support"]["score"] == 1.0
    assert annotations["hypothesis_coverage"]["score"] == 0.5
    assert annotations["critic_review"]["label"] == "ok"


def test_run_summary_annotations_flag_scope_creep():
    state = _deep_state()
    state["investigation_plan"]["items"].append({"id": "item-003", "status": "done"})

    annotations = {item["name"]: item for item in run_summary_annotations(state)}

    assert annotations["plan_execution"]["score"] == 1.0
    assert "scope creep: 3 > max_items=2" in annotations["plan_execution"]["explanation"]


def test_run_summary_annotations_chat_has_no_investigation_metrics():
    annotations = {item["name"]: item for item in run_summary_annotations(
        {"intent": {"intent": "simple", "rationale": "обычный вопрос"}}
    )}

    assert annotations["run_type"]["label"] == "chat"
    assert annotations["scope_mode"]["label"] == "none"
    assert len(annotations) == 2


def test_annotate_writes_quality_and_run_summary():
    client = PhoenixClient("http://phoenix:6006", "logicore-agent")
    client._sdk = SDK()
    record = EvaluationRecord(
        run_id="run-2",
        thread_id="thread-2",
        trace_id="trace-2",
        verdict="pass",
        score=0.9,
        summary="Ок",
        model="evaluator-model",
    )

    client.annotate(record, _deep_state())

    calls = client._sdk.traces.calls
    assert calls[0]["annotation_name"] == "logicore_agent_quality"
    summary_names = {call["annotation_name"] for call in calls[1:]}
    assert summary_names == {
        "run_type", "scope_mode", "plan_execution",
        "claims_support", "hypothesis_coverage", "critic_review",
    }
    for call in calls[1:]:
        assert call["trace_id"] == "trace-2"
        assert call["annotator_kind"] == "CODE"
        assert call["identifier"] == "run-v1"
        assert call["sync"] is True


def test_health_exposes_missing_trace_metrics(monkeypatch):
    class Store:
        enabled = True

        def summary(self):
            return {"total": 3, "missing_traces": 1, "published": 2}

    monkeypatch.setattr(evaluator_app, "store", Store())

    health = asyncio.run(evaluator_app.health())

    assert health["ok"] is True
    assert health["evaluations"]["missing_traces"] == 1
