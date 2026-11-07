import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "agent-evaluator"))

from experiments import (
    artifact_compliance,
    completed,
    dataset_examples,
    efficiency,
    evidence_compliance,
    load_scenarios,
    scope_compliance,
)


def test_golden_dataset_has_live_and_fixed_scenarios():
    scenarios = load_scenarios()

    assert len(scenarios) == 26
    assert sum("state" in item["input"] for item in scenarios) == 6
    assert sum("task" in item["input"] for item in scenarios) == 20
    examples = dataset_examples(scenarios)
    assert examples[0]["metadata"]["scenario_id"] == scenarios[0]["id"]
    assert examples[0]["output"] == scenarios[0]["expected"]


def test_fixed_scenarios_match_deterministic_evaluators():
    for scenario in load_scenarios():
        if "state" not in scenario["input"]:
            continue
        from experiments import experiment_task

        output = experiment_task(scenario["input"])
        assert completed(output) == 1
        assert evidence_compliance(output, scenario["expected"]) == 1, scenario["id"]
        assert scope_compliance(output, scenario["expected"]) == 1, scenario["id"]


def test_live_evaluators_check_scope_tools_and_repetition():
    output = {
        "kind": "run",
        "status": "success",
        "work_mode": "deep",
        "scope_mode": "discovery",
        "plan_items": 2,
        "evidence_ids": ["ev-1"],
        "claims": [{"text": "ok", "evidence_ids": ["ev-1"]}],
        "tools": ["timeline", "top_values"],
        "repeated_tool_calls": 0,
    }
    expected = {
        "work_mode": "deep",
        "scope_mode": "discovery",
        "max_items": 2,
        "min_evidence": 1,
        "required_tools": ["timeline"],
        "max_repeated_tool_calls": 0,
    }

    assert completed(output) == 1
    assert scope_compliance(output, expected) == 1
    assert evidence_compliance(output, expected) == 1
    assert efficiency(output, expected) == 1
    assert scope_compliance({**output, "plan_items": 3}, expected) == 0


def test_artifact_compliance_checks_dataset_limit_hashes_and_report_consistency():
    output = {
        "kind": "run",
        "analysis_dataset_create_calls": 1,
        "package_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "false_zip_denial": False,
        "claims_missing_evidence_class": 0,
    }
    expected = {
        "max_analysis_dataset_create": 1,
        "require_detached_hashes": True,
        "forbid_false_zip_denial": True,
        "require_evidence_classes": True,
    }

    assert artifact_compliance(output, expected) == 1
    assert artifact_compliance({**output, "analysis_dataset_create_calls": 2}, expected) == 0
    assert artifact_compliance({**output, "manifest_sha256": ""}, expected) == 0
    assert artifact_compliance({**output, "false_zip_denial": True}, expected) == 0
    assert artifact_compliance({**output, "claims_missing_evidence_class": 1}, expected) == 0
