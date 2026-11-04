import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "agent-evaluator"))

from experiments import (
    completed,
    dataset_examples,
    efficiency,
    evidence_compliance,
    load_scenarios,
    scope_compliance,
)


def test_golden_dataset_has_live_and_fixed_scenarios():
    scenarios = load_scenarios()

    assert len(scenarios) == 24
    assert sum("state" in item["input"] for item in scenarios) == 6
    assert sum("task" in item["input"] for item in scenarios) == 18
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
