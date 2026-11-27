import mp_saturn_test
from mp_saturn_test import compare_event, normalize_lf, substituted_pipelines


def test_pipeline_substitution_is_file_copy_and_restores_everything(tmp_path):
    tools = tmp_path / "tools"
    destination = tools / "docker" / "saturn-pipelines"
    destination.mkdir(parents=True)
    (destination / "original.txt").write_text("original", encoding="utf-8")
    entrypoint = tools / "docker" / "saturn" / "entrypoint.sh"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_bytes(b"#!/bin/bash\r\nexit 0\r\n")
    pipelines = tmp_path / "checkout"
    pipelines.mkdir()
    (pipelines / "base.txt").write_text("base", encoding="utf-8")
    package = tmp_path / "package"
    generated = package / "pipeline" / "parse" / "axelnac"
    generated.mkdir(parents=True)
    (generated / "rules.yml").write_text("rules: []", encoding="utf-8")

    with substituted_pipelines(tools, pipelines, package) as substituted:
        assert not (substituted / "original.txt").exists()
        assert (substituted / "base.txt").read_text(encoding="utf-8") == "base"
        assert (substituted / "pipeline" / "parse" / "axelnac" / "rules.yml").is_file()
        assert entrypoint.read_bytes() == b"#!/bin/bash\nexit 0\n"

    assert (destination / "original.txt").read_text(encoding="utf-8") == "original"
    assert not (destination / "base.txt").exists()
    assert entrypoint.read_bytes() == b"#!/bin/bash\r\nexit 0\r\n"


def test_clickhouse_comparison_checks_expected_and_required_fields():
    fixture = {
        "expected": {"id": "PT_test", "numfield1": 3},
        "required_fields": ["time", "event_src_title"],
    }
    actual = {
        "id": "PT_test",
        "numfield1": 3,
        "timestamp": "2024-10-27 12:00:00",
        "event_src_title": "axelnac",
    }

    assert compare_event(actual, fixture) == []
    actual["id"] = "syslog-3164"
    assert compare_event(actual, fixture)[0]["error"] == "value_mismatch"
    actual["id"] = "PT_test"
    actual["event_src_title"] = None
    assert compare_event(actual, fixture)[0]["error"] == "missing_or_empty"


def test_timestamp_modified_compares_original_time_and_iso_precision():
    fixture = {"expected": {
        "time": "2024-10-27T09:00:00.000Z",
        "start_time": "2024-10-27T09:00:00.000Z",
    }}
    actual = {
        "timestamp": "2026-11-25T15:24:38Z",
        "original_time": "2024-10-27T09:00:00Z",
        "start_time": "2024-10-27T09:00:00Z",
        "tags": ["timestamp_modified"],
    }

    assert compare_event(actual, fixture) == []


def test_await_event_accepts_tcp_newline(tmp_path, monkeypatch):
    queries = []

    def fake_query(root, query):
        queries.append(query)
        return [{"message": "raw\n", "id": "PT_test"}]

    monkeypatch.setattr(mp_saturn_test, "clickhouse_query", fake_query)

    actual = mp_saturn_test.await_event(tmp_path, "raw", timeout=1)

    assert actual["id"] == "PT_test"
    assert "'raw\\n'" in queries[0]


def test_lf_normalization(tmp_path):
    path = tmp_path / "entrypoint.sh"
    path.write_bytes(b"line1\r\nline2\rline3\n")
    normalize_lf(path)
    assert path.read_bytes() == b"line1\nline2\nline3\n"
