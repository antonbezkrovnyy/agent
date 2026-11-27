import json
from pathlib import Path

import mapping
import mp_text
from mp_export import compile_spec, export_package, native_expected_event, spec_from_revisions
from mp_profile import load_profile
from mp_schemas import MpTextExportSpec
import storage


CANDIDATE = Path("fixtures/axelnac/candidate_login_ok.json")


def candidate() -> MpTextExportSpec:
    return MpTextExportSpec.model_validate_json(CANDIDATE.read_text(encoding="utf-8"))


def test_compiler_is_deterministic_and_builds_native_fixtures():
    first = compile_spec(candidate())
    second = compile_spec(candidate())

    assert first["ok"] is True
    assert first["yaml"] == second["yaml"]
    assert first["conf"] == second["conf"]
    assert first["spec_hash"] == second["spec_hash"]
    assert 'kind: TEXT' in first["yaml"]
    assert 'uuid: a5117013-9aaa-5089-89d2-89a87522977e' in first["yaml"]
    assert '."message"' not in first["yaml"]
    assert '."uuid"' not in first["yaml"]
    assert 'subformulas: null' in first["yaml"]
    assert first["positive_fixtures"] == 1
    assert first["negative_fixtures"] == 1
    assert first["fixtures"]["positive"][0]["expected"]["numfield1"] == 1
    assert "$user" in first["fixtures"]["positive"][0]["unexpected_fields"]
    assert "Login OK:" in first["conf"]
    assert "parse_axelnac_specific" in first["conf"]


def test_pilot_candidate_covers_login_and_failure_routes():
    spec = MpTextExportSpec.model_validate_json(
        Path("fixtures/axelnac/candidate_pilot.json").read_text(encoding="utf-8")
    )

    result = compile_spec(spec)

    assert result["ok"] is True
    assert result["rules"] == 4
    assert result["positive_fixtures"] == 4
    assert '"Login incorrect" in [message]' in result["conf"]


def test_event_id_must_follow_pt_policy():
    data = candidate().model_dump(mode="json")
    for mapping in data["rules"][0]["mappings"]:
        if mapping["target"] == "id":
            mapping["constant"] = "SIEM_AxelNAC_Auth_Syslog_Login"

    result = compile_spec(MpTextExportSpec.model_validate(data))

    assert result["ok"] is False
    assert any(item["error"] == "invalid_event_id" for item in result["issues"])


def test_naive_syslog_time_is_reinterpreted_in_source_timezone():
    result = compile_spec(candidate())

    assert result["ok"] is True
    assert 'format_timestamp!(."$time"' in result["yaml"]
    assert 'timezone: "Europe/Moscow"' in result["yaml"]


def test_unsupported_capability_has_exact_ir_path():
    data = candidate().model_dump(mode="json")
    data["rules"][0]["normalization"][1]["operations"] = [
        {"op": "normalize_ip", "params": {"version": "any"}}
    ]

    result = compile_spec(MpTextExportSpec.model_validate(data))

    assert result["ok"] is False
    assert result["issues"] == [{
        "path": "rules.0.normalization.1.operations.0",
        "error": "unsupported_capability",
        "capability": "normalize_ip_canonicalization",
        "recovery": "Используйте MP IPV4/IPV6 capture и проверьте expected event; canonicalization пока не понижается.",
    }]


def test_compiler_rejects_saturn_managed_and_unknown_fields():
    data = candidate().model_dump(mode="json")
    data["rules"][0]["mappings"].append({
        "source": None,
        "target": "uuid",
        "constant": "00000000-0000-0000-0000-000000000000",
        "description": "Invalid attempt to override Saturn UUID.",
    })
    data["rules"][0]["mappings"].append({
        "source": None,
        "target": "invented.field",
        "constant": "value",
        "description": "Invalid invented taxonomy field.",
    })

    result = compile_spec(MpTextExportSpec.model_validate(data))

    assert result["ok"] is False
    errors = {item["error"] for item in result["issues"]}
    assert "saturn_managed_field_forbidden:uuid" in errors
    assert "unknown_logicore_field:invented.field" in errors


def test_dispatch_must_cover_every_positive_raw():
    data = candidate().model_dump(mode="json")
    data["rules"][0]["evidence"].append("unrelated message")
    data["rules"][0]["expected_events"].append({})

    result = compile_spec(MpTextExportSpec.model_validate(data))

    assert result["ok"] is False
    assert any(
        item["error"] == "dispatch_anchor_does_not_cover_evidence"
        and item["path"] == "rules.0.evidence.1"
        for item in result["issues"]
    )


def test_export_spec_is_assembled_from_existing_revisions(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_DB", str(tmp_path / "registry.db"))
    spec = candidate()
    rule = spec.rules[0]
    family_description = {
        "summary": "AxelNAC login",
        "event_semantics": "Login",
        "envelope_and_payload": "Syslog",
        "dispatch": "Login OK",
        "applicability": "login_ok",
        "exclusions": "other",
    }
    parser_description = {
        "summary": "MP TEXT",
        "input_contract": "RAW",
        "layering": "native MP TEXT",
        "output_contract": "captures",
        "revision_reason": "initial",
        "regression_and_limitations": "native evidence",
    }
    storage.save_family(
        "axelnac_login_ok", 1, [], [],
        [field.model_dump(mode="json") for field in rule.record_schema],
        rule.evidence,
        family_description,
        parser_description,
        [],
        [],
        target="mp_text",
        text_pattern=rule.pattern,
        negative_evidence=rule.negative_evidence,
    )
    storage.save_normalization(
        "axelnac_login_ok", 2, 1,
        [field.model_dump(mode="json") for field in rule.normalization],
        rule.evidence,
        "Normalization revision",
    )
    storage.save_mapping(
        "axelnac_login_ok", 3, 1, 2, "mp_siem_8.0", spec.source_timezone,
        [item.model_dump(mode="json") for item in rule.mappings],
        rule.evidence,
        "Mapping revision",
    )
    monkeypatch.setattr(mapping, "evaluate_mappings", lambda *args: {
        "ok": True,
        "results": [{"event": rule.expected_events[0]}],
    })
    monkeypatch.setattr(mp_text, "execute_pattern", lambda *args: {
        "ok": True,
        "captures": {"time": "2024-10-27T12:00:00Z"},
    })

    assembled = spec_from_revisions("axelnac", "axelnac_login_ok")

    assert assembled.source == "axelnac"
    assert assembled.revision == 3
    assert assembled.rules[0].pattern == rule.pattern
    assert assembled.rules[0].expected_events == rule.expected_events
    assert "Login OK:" in assembled.dispatch_contains
    assert assembled.rules[0].native_expected_events[0]["time"] == "2024-10-27T12:00:00.000Z"
    assert compile_spec(assembled)["ok"] is True


def test_native_expected_projects_mac_and_datetime():
    event = {
        "src.mac": "aa:bb:cc:dd:ee:ff",
        "time": "2024-10-27T12:00:00Z",
    }

    assert native_expected_event(event, load_profile()) == {
        "src_mac": "AA:BB:CC:DD:EE:FF",
        "time": "2024-10-27T12:00:00.000Z",
    }


def test_no_native_export_is_explicitly_a_draft(tmp_path):
    result = export_package(candidate(), tmp_path, native=False)

    assert result["ok"] is False
    assert result["error"] == "native_validation_required"
    assert Path(result["rules_path"]).is_file()
    assert Path(result["conf_path"]).is_file()
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert report["native"]["status"] == "skipped"
