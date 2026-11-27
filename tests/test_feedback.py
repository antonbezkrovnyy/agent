from langchain_core.messages import AIMessage
import pytest

import agent as agent_module
import corpus
import mapping_agent
import normalization_agent
import prompts
import storage
import tools
from schemas import (
    GrokLayer,
    MappingRevision,
    NormalizationRevision,
    ParserDefinition,
    RecordField,
    Response,
    StructureLayer,
)


@pytest.fixture(autouse=True)
def research_database(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_DB", str(tmp_path / "feedback.db"))


class FakeAgent:
    def __init__(self, *contents):
        self.contents = contents
        self.calls = 0

    def stream(self, *args, **kwargs):
        content = self.contents[min(self.calls, len(self.contents) - 1)]
        self.calls += 1
        yield {
            "type": "values",
            "data": {"messages": [AIMessage(content=content)]},
        }


def parser_response() -> Response:
    return Response(
        essence="raw observation",
        structure=[StructureLayer(
            path="$",
            format="raw",
            role="value",
            transform=None,
            confidence="observed",
        )],
        strategy=[],
        record_schema=[RecordField(
            name="raw",
            category="dynamic",
            type="string",
            required=True,
            source="$",
        )],
        format_features=[],
        pitfalls=[],
        parser=ParserDefinition(
            kind="grok",
            layers=[GrokLayer(name="raw", source="raw", pattern="alpha")],
        ),
    )


def test_model_tool_reports_deduplicates_and_resolves_feedback():
    arguments = {
        "agent": "mapping",
        "category": "ambiguous_semantics",
        "summary": "Не подтверждён смысл поля protocol.",
        "family_id": "example_family",
        "parser_revision": 2,
        "normalization_revision": 3,
        "mapping_revision": 4,
        "field": "protocol",
        "evidence": ["raw one"],
        "details": {"observed_values": ["IP", "UDP"]},
        "needed": "Нужны документация и дополнительные RAW.",
    }

    first = tools.report_feedback.invoke(arguments)
    second = tools.report_feedback.invoke({
        **arguments,
        "evidence": ["raw two"],
        "blocking": True,
    })

    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert second["id"] == first["id"]
    entry = second["feedback"]
    assert entry["source"] == "model"
    assert entry["status"] == "open"
    assert entry["blocking"] is True
    assert entry["occurrences"] == 2
    assert entry["evidence"] == ["raw one", "raw two"]
    assert entry["details"] == {"observed_values": ["IP", "UDP"]}
    assert storage.list_feedback(family_id="example_family")["count"] == 1

    resolved = storage.resolve_feedback(first["id"], "Смысл подтверждён документацией и regression.")

    assert resolved["feedback"]["status"] == "resolved"
    assert resolved["feedback"]["resolved_at"] is not None
    assert storage.list_feedback()["count"] == 0
    assert storage.list_feedback(status="resolved")["count"] == 1

    reopened = tools.report_feedback.invoke(arguments)

    assert reopened["id"] != first["id"]
    assert reopened["feedback"]["occurrences"] == 1
    assert reopened["feedback"]["status"] == "open"


def test_feedback_can_remain_accepted_uncertainty():
    reported = storage.report_feedback(
        source="model",
        agent="normalization",
        category="missing_configuration",
        summary="В timestamp отсутствует год.",
        evidence=["Feb 12 13:00:00 host message"],
    )

    resolved = storage.resolve_feedback(
        reported["id"],
        "Год не выдумывается; исходное значение сохранено без ISO-преобразования.",
        status="accepted_uncertainty",
    )

    assert resolved["feedback"]["status"] == "accepted_uncertainty"
    assert storage.list_feedback()["count"] == 0
    assert storage.list_feedback(status="accepted_uncertainty")["count"] == 1


def test_parser_runner_closes_invalid_response_after_successful_retry(monkeypatch):
    expected = parser_response()
    fake_agent = FakeAgent("not json", expected.model_dump_json())
    monkeypatch.setattr(agent_module, "check_response", lambda response, raw: None)

    actual = agent_module.run_step(fake_agent, [], "alpha", show_response=False, show_tools=False)

    assert actual == expected
    assert fake_agent.calls == 2
    assert storage.list_feedback()["count"] == 0
    resolved = storage.list_feedback(status="resolved")
    assert resolved["count"] == 1
    assert resolved["feedback"][0]["category"] == "invalid_response"
    assert resolved["feedback"][0]["source"] == "runner"
    assert resolved["feedback"][0]["evidence"] == ["alpha"]


def test_parser_runner_closes_validation_problem_after_successful_retry(monkeypatch):
    expected = parser_response()
    problems = iter([
        {
            "error": "parser_validation_failed",
            "message": "Candidate не прошёл RAW.",
            "recovery": "Исправь parser.",
            "details": {"failed_layer": "raw"},
        },
        None,
    ])
    monkeypatch.setattr(agent_module, "check_response", lambda response, raw: next(problems))

    actual = agent_module.run_step(
        FakeAgent(expected.model_dump_json()),
        [],
        "alpha",
        show_response=False,
        show_tools=False,
    )

    assert actual == expected
    resolved = storage.list_feedback(status="resolved")
    assert resolved["count"] == 1
    assert resolved["feedback"][0]["category"] == "parser_validation_failed"
    assert resolved["feedback"][0]["details"] == {"failed_layer": "raw"}


def test_normalization_runner_closes_invalid_response_after_successful_retry(monkeypatch):
    expected = NormalizationRevision(
        family_id="example_family",
        revision=1,
        parser_revision=2,
        fields=[],
        evidence=["raw"],
        description="test",
    )
    monkeypatch.setattr(normalization_agent, "check_revision", lambda response: None)
    monkeypatch.setattr(normalization_agent.storage, "save_research", lambda payload: {"id": 10})

    actual, research_id = normalization_agent.run_normalization(
        FakeAgent("not json", expected.model_dump_json()),
        "example_family",
        2,
    )

    assert actual == expected
    assert research_id == 10
    resolved = storage.list_feedback(status="resolved")
    assert resolved["count"] == 1
    assert resolved["feedback"][0]["agent"] == "normalization"
    assert resolved["feedback"][0]["category"] == "invalid_response"


def test_mapping_runner_closes_invalid_response_after_successful_retry(monkeypatch):
    expected = MappingRevision(
        family_id="example_family",
        revision=4,
        parser_revision=2,
        normalization_revision=3,
        taxonomy="mp_siem_8.0",
        mappings=[],
        evidence=["raw"],
        description="test",
    )
    monkeypatch.setattr(mapping_agent, "check_revision", lambda response: None)
    monkeypatch.setattr(mapping_agent.storage, "save_research", lambda payload: {"id": 11})

    actual, research_id = mapping_agent.run_mapping(
        FakeAgent("not json", expected.model_dump_json()),
        "example_family",
        2,
        3,
    )

    assert actual == expected
    assert research_id == 11
    resolved = storage.list_feedback(status="resolved")
    assert resolved["count"] == 1
    assert resolved["feedback"][0]["agent"] == "mapping"
    assert resolved["feedback"][0]["category"] == "invalid_response"


def test_unexpected_research_failure_remains_open_and_blocking(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(agent_module, "run_step", fail)

    with pytest.raises(RuntimeError, match="model unavailable"):
        agent_module.run_observation(None, "raw evidence")

    opened = storage.list_feedback()
    assert opened["count"] == 1
    entry = opened["feedback"][0]
    assert entry["category"] == "research_failed"
    assert entry["blocking"] is True
    assert entry["status"] == "open"
    assert entry["evidence"] == ["raw evidence"]


def test_corpus_records_normalization_failure(monkeypatch):
    monkeypatch.setattr(
        corpus.storage,
        "get_normalization",
        lambda *args, **kwargs: {"ok": True, "normalization": {"revision": 3}},
    )
    monkeypatch.setattr(
        corpus.normalization,
        "normalize_with_latest",
        lambda *args: {
            "status": "normalization_failed",
            "normalization_revision": 3,
            "errors": [{"source": "protocol", "error": "unknown value"}],
        },
    )

    result = corpus.apply_normalization(object(), "example_family", 2, "raw evidence")

    assert result["status"] == "normalization_failed"
    entry = storage.list_feedback()["feedback"][0]
    assert entry["agent"] == "normalization"
    assert entry["category"] == "normalization_failed"
    assert entry["normalization_revision"] == 3
    assert entry["field"] == "protocol"
    assert entry["blocking"] is True


def test_corpus_records_mapping_failure(monkeypatch):
    monkeypatch.setattr(
        corpus.storage,
        "get_normalization",
        lambda *args, **kwargs: {"ok": True, "normalization": {"revision": 3}},
    )
    monkeypatch.setattr(corpus.storage, "get_mapping", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(
        corpus.mapping,
        "map_with_latest",
        lambda *args: {
            "status": "mapping_failed",
            "mapping_revision": 4,
            "taxonomy": "mp_siem_8.0",
            "errors": [{"source": "protocol", "error": "unmapped_source_value:UDP"}],
        },
    )

    result = corpus.apply_mapping(object(), "example_family", 2, "raw evidence", "UTC")

    assert result["status"] == "mapping_failed"
    entry = storage.list_feedback()["feedback"][0]
    assert entry["agent"] == "mapping"
    assert entry["category"] == "mapping_failed"
    assert entry["normalization_revision"] == 3
    assert entry["mapping_revision"] == 4
    assert entry["field"] == "protocol"
    assert entry["details"] == {
        "errors": [{"source": "protocol", "error": "unmapped_source_value:UDP"}]
    }


def test_feedback_tools_and_instructions_are_available_to_all_single_agents():
    for toolset in (tools.TOOLS, tools.NORMALIZATION_TOOLS, tools.MAPPING_TOOLS):
        names = {item.name for item in toolset}
        assert {"report_feedback", "list_open_feedback", "get_feedback"} <= names

    assert "agent=parser" in prompts.SYSTEM_PROMPT
    assert "agent=normalization" in prompts.NORMALIZATION_SYSTEM_PROMPT
    assert "agent=mapping" in prompts.MAPPING_SYSTEM_PROMPT
