import json

import mp_native


def test_decode_json_report_ignores_cargo_output():
    report = mp_native.decode_json_report(
        "    Compiling mpsiem\n"
        "warning: build output\n"
        + json.dumps({"ok": True, "declared_rules": 2})
    )

    assert report == {"ok": True, "declared_rules": 2}


def test_write_crate_uses_selected_mpsiem_source(tmp_path):
    mpsiem = tmp_path / "mpsiem source"
    mpsiem.mkdir()
    crate = tmp_path / "validator"

    mp_native.write_crate(crate, mpsiem)

    manifest = (crate / "Cargo.toml").read_text(encoding="utf-8")
    assert 'name = "mp_rule_validator"' in manifest
    assert mpsiem.resolve().as_posix() in manifest
    assert (crate / "src" / "main.rs").read_bytes() == mp_native.VALIDATOR_SOURCE.read_bytes()


def test_missing_mpsiem_source_is_reported_without_starting_process(tmp_path):
    result = mp_native.run_validator(["capabilities"], tmp_path / "missing", mode="local")

    assert result["ok"] is False
    assert result["error"] == "mpsiem_source_not_found"
