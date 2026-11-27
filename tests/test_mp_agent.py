import json
from pathlib import Path

import pytest

import mp_agent
import mp_native
from mp_schemas import MpTextCandidateResponse
from mp_tools import make_mp_text_tools
import storage
from test_mp_export import candidate


@pytest.fixture(autouse=True)
def export_database(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_DB", str(tmp_path / "exports.db"))


def fake_native_success(yaml_text, fixtures, **kwargs):
    return {
        "ok": True,
        "strict_load": True,
        "declared_rules": 1,
        "loaded_rules": 1,
        "skipped_rules": 0,
        "positive_fixtures": len(fixtures["positive"]),
        "negative_fixtures": len(fixtures["negative"]),
        "failed_fixtures": 0,
        "results": [],
    }


def test_agent_tools_evaluate_export_and_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(mp_native, "validate_rules_from_text", fake_native_success)
    tools = {
        item.name: item
        for item in make_mp_text_tools(tmp_path, mpsiem=tmp_path / "unused", native_mode="docker")
    }

    evaluated = tools["evaluate_mp_text_candidate"].invoke({"spec": candidate().model_dump(mode="json")})
    assert evaluated["ok"] is True

    exported = tools["export_mp_normalizer"].invoke({"spec": candidate().model_dump(mode="json")})
    assert exported["ok"] is True
    assert exported["native"]["skipped_rules"] == 0
    assert Path(exported["rules_path"]).is_file()
    assert storage.get_mp_export("axelnac", 1)["export"]["spec_hash"] == exported["spec_hash"]

    response = MpTextCandidateResponse(
        source="axelnac",
        revision=1,
        yaml_path=exported["rules_path"],
        conf_path=exported["conf_path"],
        fixtures_path=exported["fixtures_path"],
        report_path=exported["report_path"],
        native_ok=True,
        rules=1,
        positive_fixtures=1,
        negative_fixtures=1,
        assumptions=[],
    )
    assert mp_agent.check_export(response) is None

    blocking = storage.report_feedback(
        source="runner",
        agent="mp_text",
        category="test_blocking",
        summary="Unresolved production problem.",
        family_id="axelnac",
        blocking=True,
    )
    problem = mp_agent.check_export(response)
    assert problem["error"] == "mp_export_has_blocking_feedback"
    storage.resolve_feedback(blocking["id"], "Problem fixed.")
    assert mp_agent.check_export(response) is None


def test_taxonomy_is_seeded_for_a_new_registry():
    assert storage.list_taxonomy_fields("mp_siem_8.0") == []

    mp_agent.ensure_taxonomy_loaded()

    assert storage.get_taxonomy_field("mp_siem_8.0", "action")["ok"] is True
    assert storage.get_taxonomy_values("mp_siem_8.0", "action")["values"]
    assert storage.get_taxonomy_categories("mp_siem_8.0")["categories"]


def test_export_revision_cannot_be_rewritten(tmp_path, monkeypatch):
    monkeypatch.setattr(mp_native, "validate_rules_from_text", fake_native_success)
    export_tool = next(
        item for item in make_mp_text_tools(tmp_path) if item.name == "export_mp_normalizer"
    )
    spec = candidate()
    first = export_tool.invoke({"spec": spec.model_dump(mode="json")})
    assert first["ok"] is True

    changed = spec.model_dump(mode="json")
    changed["assumptions"] = ["changed"]
    second = export_tool.invoke({"spec": changed})
    assert second["ok"] is False
    assert second["error"] == "mp_export_revision_exists_with_different_content"


def test_full_corpus_gate_updates_report(tmp_path, monkeypatch):
    monkeypatch.setattr(mp_native, "validate_rules_from_text", fake_native_success)
    export_tool = next(
        item for item in make_mp_text_tools(tmp_path) if item.name == "export_mp_normalizer"
    )
    exported = export_tool.invoke({"spec": candidate().model_dump(mode="json")})
    response = MpTextCandidateResponse(
        source="axelnac",
        revision=1,
        yaml_path=exported["rules_path"],
        conf_path=exported["conf_path"],
        fixtures_path=exported["fixtures_path"],
        report_path=exported["report_path"],
        native_ok=True,
        rules=1,
        positive_fixtures=1,
        negative_fixtures=1,
        assumptions=[],
    )
    raw = candidate().rules[0].evidence

    report = mp_agent.validate_full_corpus(response, raw, Path("unused"), "docker")

    assert report["ok"] is True
    package = json.loads(Path(response.report_path).read_text(encoding="utf-8"))
    assert package["corpus"]["ok"] is True
    assert package["corpus"]["total"] == 1
