import pytest

import corpus
import mp_native
import mp_text
import runtime
import semantic
import storage
import tools
from schemas import Observation, ParserDefinition, RecordField, Response, StructureLayer
from validation import check_response


@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_DB", str(tmp_path / "registry.db"))
    monkeypatch.setattr(semantic, "index_family", lambda *args: None)


@pytest.fixture
def fake_native(monkeypatch):
    def execute(document, **kwargs):
        results = []
        for rule in document["rules"]:
            for fixture in rule.get("positive", []):
                results.append({
                    "ok": True,
                    "matched_rules": [0],
                    "captures": fixture.get("expected", {}),
                    "errors": [],
                })
            for fixture in rule.get("negative", []):
                results.append({
                    "ok": True,
                    "matched_rules": [],
                    "captures": {},
                    "errors": [],
                })
        return {"ok": True, "results": results}

    monkeypatch.setattr(mp_native, "execute_text_captures", execute)
    return execute


def descriptions():
    family = {
        "summary": "Test family",
        "event_semantics": "Test event",
        "envelope_and_payload": "Raw text",
        "dispatch": "alpha",
        "applicability": "alpha",
        "exclusions": "other",
    }
    parser = {
        "summary": "MP TEXT parser",
        "input_contract": "raw alpha",
        "layering": "single native MP TEXT pattern",
        "output_contract": "raw evidence",
        "revision_reason": "initial target support",
        "regression_and_limitations": "native positive and negative fixtures",
    }
    return family, parser


def test_parser_definition_accepts_native_mode_without_research_layers():
    parser = ParserDefinition(kind="mp_text", text_pattern="alpha")

    assert parser.text_pattern == "alpha"
    assert parser.layers == []
    assert parser.stages == []
    with pytest.raises(ValueError, match="patterns/layers/stages"):
        ParserDefinition(kind="mp_text", text_pattern="alpha", layers=[
            {"name": "raw", "source": "raw", "pattern": "alpha"}
        ])


def test_native_result_preserves_existing_parser_result_contract(fake_native):
    result = mp_text.evaluate_observations(
        "alpha {$user=WORD}",
        [RecordField(
            name="user", category="dynamic", type="string", required=True, source="$.user"
        )],
        [Observation(raw="alpha alice", expected={"user": "alice"})],
        negative_evidence=["beta"],
    )

    assert result["ok"] is True
    assert result["results"][0]["captures"] == {"user": "alice"}
    assert result["results"][1]["negative"] is True


def test_save_and_validate_mp_text_family_through_existing_registry(fake_native):
    family_description, parser_description = descriptions()
    saved = tools.save_mp_text_family.invoke({
        "family_id": "native_alpha",
        "revision": 1,
        "text_pattern": "alpha",
        "record_schema": [{
            "name": "raw", "category": "dynamic", "type": "string", "required": True, "source": "$"
        }],
        "evidence": ["alpha"],
        "family_description": family_description,
        "parser_description": parser_description,
        "negative_evidence": ["beta"],
    })

    assert saved["ok"] is True
    family = storage.get_family("native_alpha", 1)["family"]
    assert family["target"] == "mp_text"
    assert family["text_pattern"] == "alpha"
    assert family["negative_evidence"] == ["beta"]

    response = Response(
        essence="native alpha observation",
        structure=[StructureLayer(
            path="$", format="raw", role="value", transform=None, confidence="observed"
        )],
        strategy=[],
        record_schema=[RecordField(
            name="raw", category="dynamic", type="string", required=True, source="$"
        )],
        format_features=[],
        pitfalls=[],
        parser=ParserDefinition(
            kind="mp_text", text_pattern="alpha", negative_evidence=["beta"]
        ),
    )
    assert check_response(response, "alpha") is None


def test_target_filter_does_not_mix_research_and_mp_text(fake_native):
    family_description, parser_description = descriptions()
    tools.save_mp_text_family.invoke({
        "family_id": "native_alpha",
        "revision": 1,
        "text_pattern": "alpha",
        "record_schema": [{
            "name": "raw", "category": "dynamic", "type": "string", "required": True, "source": "$"
        }],
        "evidence": ["alpha"],
        "family_description": family_description,
        "parser_description": parser_description,
    })
    storage.save_family(
        "research_alpha",
        1,
        [],
        [{"name": "raw", "source": "raw", "pattern": "alpha"}],
        [{"name": "raw", "category": "dynamic", "type": "string", "required": True, "source": "$"}],
        ["alpha"],
        family_description,
        parser_description,
        [],
    )

    research = corpus.matching_parsers("alpha", "research")
    native = corpus.matching_parsers("alpha", "mp_text")

    assert [item["family_id"] for item in research] == ["research_alpha"]
    assert [item["family_id"] for item in native] == ["native_alpha"]
    assert runtime.execute_family(storage.get_family("native_alpha")["family"], "alpha")["ok"] is True
