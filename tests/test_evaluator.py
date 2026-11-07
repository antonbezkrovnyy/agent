import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("AGENT_MEMORY_DATABASE_URL", "postgresql://test/test")

sys.path.insert(0, str(Path(__file__).parents[1] / "agent-evaluator"))

import app as evaluator_app
from evaluator import deterministic_checks, evaluate
from models import EvaluationRecord, EvaluationResult
from phoenix_client import compact_trace


class Settings:
    llm_model_evaluator = "evaluator-model"
    llm_model = "base-model"
    llm_base_url = "http://localhost"
    llm_token = "test"
    evaluator_timeout_seconds = 10


class FakeLLM:
    def with_structured_output(self, schema, method):
        return self

    def invoke(self, messages):
        return EvaluationResult(verdict="pass", score=0.95, summary="Замечаний нет", issues=[])


def trace(status="OK"):
    return {
        "traceId": "trace-1",
        "numSpans": 1,
        "latencyMs": 1200,
        "spans": [{
            "spanId": "span-1",
            "statusCode": status,
            "statusMessage": "",
            "name": "reporter",
            "tokenCountTotal": 42,
            "attributes": "{}",
        }],
    }


def test_deterministic_checks_capture_claim_and_trace_errors():
    state = {
        "evidence": [{"id": "ev-partial", "status": "partial", "complete": False}],
        "report_result": {"claims": [{"text": "Вывод", "evidence_ids": ["missing"]}]},
        "memory_error": "Память недоступна",
        "critic": {"verdict": "critic_unavailable"},
        "memory_operations": [],
    }

    issues, metrics = deterministic_checks(state, trace("ERROR"))

    assert {item.category for item in issues} >= {"runtime", "unsupported_claim", "missing_data", "memory"}
    assert metrics["trace_errors"] == 1
    assert metrics["tokens_total"] == 42
    assert metrics["incomplete_evidence_count"] == 1


def test_critical_deterministic_issue_overrides_llm_pass(monkeypatch):
    monkeypatch.setattr("evaluator._llm", lambda settings: FakeLLM())
    state = {
        "evidence": [],
        "report_result": {"claims": [{"text": "Нет доказательств", "evidence_ids": ["missing"]}]},
    }

    result = evaluate(state, trace(), Settings())

    assert result.verdict == "fail"
    assert result.score <= 0.49
    assert result.issues[0].category == "unsupported_claim"


def test_simple_chat_does_not_require_evidence(monkeypatch):
    monkeypatch.setattr("evaluator._llm", lambda settings: FakeLLM())

    result = evaluate({"intent": {"label": "simple"}, "report_result": {}}, trace(), Settings())

    assert result.verdict == "pass"
    assert result.score == 0.95


def test_compact_trace_limits_attributes():
    payload = compact_trace({
        "traceId": "trace-1",
        "spans": [{"spanId": "span-1", "attributes": "x" * 100}],
    }, attribute_limit=10)
    assert payload["spans"][0]["attributes"] == "x" * 10
    assert payload["spans"][0]["attributes_truncated"] is True


class Response:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class PublishClient:
    def __init__(self, run_id="run-1", status="success"):
        self.run_id = run_id
        self.status = status
        self.posts = []

    async def get(self, url, **kwargs):
        return Response([{"run_id": self.run_id, "status": self.status}])

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return Response({})


class Store:
    def __init__(self):
        self.published = []

    def save(self, record):
        pass

    def mark_published(self, run_id):
        self.published.append(run_id)


def record():
    return EvaluationRecord(
        run_id="run-1",
        thread_id="thread-1",
        trace_id="trace-1",
        verdict="pass",
        score=0.95,
        summary="Проверка пройдена",
        metrics={"phoenix_project_id": "project-1"},
    )


def test_publish_appends_evaluator_message_and_state(monkeypatch):
    store = Store()
    monkeypatch.setattr(evaluator_app, "store", store)
    client = PublishClient()
    item = record()

    asyncio.run(evaluator_app._publish(client, item))

    values = client.posts[0][1]["json"]["values"]
    assert values["messages"][0]["name"] == "evaluator"
    assert values["evaluation"]["run_id"] == "run-1"
    assert values["messages"][0]["content"].startswith("## Автодиагностика")
    assert "Phoenix" in values["messages"][0]["content"]
    assert store.published == ["run-1"]


def test_publish_does_not_touch_thread_with_newer_run(monkeypatch):
    store = Store()
    monkeypatch.setattr(evaluator_app, "store", store)
    client = PublishClient(run_id="run-new")
    item = record()

    asyncio.run(evaluator_app._publish(client, item))

    assert client.posts == []
    assert item.publication_error
    assert store.published == []


def test_evaluator_prompt_bounds_and_selects_evidence(monkeypatch):
    class CapturingLLM(FakeLLM):
        payload = ""

        def invoke(self, messages):
            self.payload = messages[-1].content
            return super().invoke(messages)

    model = CapturingLLM()
    monkeypatch.setattr("evaluator._llm", lambda settings: model)
    state = {
        "evidence": [{
            "id": "ev-1",
            "status": "ok",
            "complete": True,
            "result": {"events": [{"uuid": f"event-{i}"} for i in range(60)]},
        }, {
            "id": "ev-2",
            "status": "ok",
            "complete": True,
            "result": {"marker": "unrelated"},
        }],
        "report_result": {
            "answer": "Вывод",
            "claims": [{"text": "Вывод", "evidence_ids": ["ev-1"]}],
        },
    }

    evaluate(state, trace(), Settings())

    assert "event-49" in model.payload
    assert "event-50" not in model.payload
    assert "ещё 10 элементов" in model.payload
    assert "unrelated" not in model.payload
