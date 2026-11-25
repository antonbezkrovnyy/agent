from langchain_core.messages import AIMessage

import agent as agent_module
from agent import run_research_batch
from batch_corpus import SourceLog, make_research_batch
from log_batching_drain3 import IndexedClusterBatch, prepare_indexed_log_batches
from schemas import (
    FamilyResearchGroup,
    GrokLayer,
    LogSample,
    ParserDefinition,
    RecordField,
    ResearchBatch,
    ResearchBatchResponse,
    Response,
    StructureLayer,
)
import validation
from validation import check_batch_response


def make_response(marker: str) -> Response:
    return Response(
        essence=marker,
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
            layers=[GrokLayer(name="raw", source="raw", pattern=marker)],
        ),
    )


def make_batch() -> ResearchBatch:
    return ResearchBatch(
        cluster_id=7,
        total_logs_in_cluster=3,
        samples=[
            LogSample(sample_id="s0", raw="alpha one"),
            LogSample(sample_id="s1", raw="beta two"),
            LogSample(sample_id="s2", raw="alpha three"),
        ],
    )


def test_batch_response_validates_each_family_against_only_its_samples(monkeypatch):
    calls = []
    monkeypatch.setattr(
        validation,
        "check_response",
        lambda response, raws: calls.append((response.essence, raws)) or None,
    )
    response = ResearchBatchResponse(groups=[
        FamilyResearchGroup(sample_ids=["s0", "s2"], response=make_response("alpha")),
        FamilyResearchGroup(sample_ids=["s1"], response=make_response("beta")),
    ])

    assert check_batch_response(response, make_batch()) is None
    assert calls == [
        ("alpha", ["alpha one", "alpha three"]),
        ("beta", ["beta two"]),
    ]


def test_batch_response_rejects_duplicate_unknown_and_missing_assignments(monkeypatch):
    monkeypatch.setattr(
        validation,
        "check_response",
        lambda *args: (_ for _ in ()).throw(AssertionError("parser не должен исполняться")),
    )
    response = ResearchBatchResponse(groups=[
        FamilyResearchGroup(sample_ids=["s0", "s1"], response=make_response("alpha")),
        FamilyResearchGroup(sample_ids=["s1", "unknown"], response=make_response("beta")),
    ])

    problem = check_batch_response(response, make_batch())

    assert problem["error"] == "batch_assignment_invalid"
    assert problem["details"] == {
        "duplicate_sample_ids": ["s1"],
        "unknown_sample_ids": ["unknown"],
        "missing_sample_ids": ["s2"],
    }


def test_unresolved_samples_do_not_require_a_family(monkeypatch):
    calls = []
    monkeypatch.setattr(
        validation,
        "check_response",
        lambda response, raws: calls.append(raws) or None,
    )
    response = ResearchBatchResponse(
        groups=[FamilyResearchGroup(sample_ids=["s0", "s2"], response=make_response("alpha"))],
        unresolved_sample_ids=["s1"],
    )

    assert check_batch_response(response, make_batch()) is None
    assert calls == [["alpha one", "alpha three"]]


def test_batch_runner_saves_each_family_as_an_ordinary_response(monkeypatch):
    expected = ResearchBatchResponse(groups=[
        FamilyResearchGroup(sample_ids=["s0", "s2"], response=make_response("alpha")),
        FamilyResearchGroup(sample_ids=["s1"], response=make_response("beta")),
    ])

    class FakeAgent:
        def stream(self, *args, **kwargs):
            yield {
                "type": "values",
                "data": {"messages": [AIMessage(content=expected.model_dump_json())]},
            }

    saved = []
    monkeypatch.setattr(agent_module, "check_batch_response", lambda response, batch: None)

    def save_research(payload):
        saved.append(payload)
        return {"id": len(saved) + 10}

    monkeypatch.setattr(agent_module.storage, "save_research", save_research)

    actual, research_ids = run_research_batch(
        FakeAgent(),
        make_batch(),
        show_response=False,
        show_tools=False,
    )

    assert actual == expected
    assert research_ids == [11, 12]
    assert [payload["essence"] for payload in saved] == ["alpha", "beta"]
    assert all("sample_ids" not in payload for payload in saved)


def test_indexed_batches_preserve_duplicate_lineage_and_original_raw():
    batches = prepare_indexed_log_batches(["same raw", "same raw"])
    indexed = batches[0]
    indexed = IndexedClusterBatch(
        cluster_id=indexed.cluster_id,
        total_logs_in_cluster=2,
        samples=indexed.samples,
        member_indices=indexed.member_indices,
        sample_indices=indexed.sample_indices,
    )
    source_logs = [SourceLog(line=4, raw="same raw"), SourceLog(line=8, raw="same raw")]

    research_batch = make_research_batch(indexed, source_logs)

    assert indexed.member_indices == [0, 1]
    assert indexed.sample_indices == [0]
    assert research_batch.samples == [LogSample(sample_id="line:4", raw="same raw")]
