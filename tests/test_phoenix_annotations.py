import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("AGENT_MEMORY_DATABASE_URL", "postgresql://test/test")
sys.path.insert(0, str(Path(__file__).parents[1] / "agent-evaluator"))

import app as evaluator_app
from models import EvaluationRecord
from phoenix_client import PhoenixClient


class Traces:
    def __init__(self):
        self.calls = []

    def add_trace_annotation(self, **kwargs):
        self.calls.append(kwargs)


class SDK:
    def __init__(self):
        self.traces = Traces()


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


def test_health_exposes_missing_trace_metrics(monkeypatch):
    class Store:
        enabled = True

        def summary(self):
            return {"total": 3, "missing_traces": 1, "published": 2}

    monkeypatch.setattr(evaluator_app, "store", Store())

    health = asyncio.run(evaluator_app.health())

    assert health["ok"] is True
    assert health["evaluations"]["missing_traces"] == 1
