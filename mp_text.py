import json
from typing import Any

import grok
import mp_native
from schemas import FieldConstraint, Observation, ParserBaseline, ParserResult, RecordField


def capture_document(
    pattern: str,
    observations: list[Observation],
    negative_evidence: list[str] | None = None,
    name: str = "candidate",
) -> dict[str, Any]:
    return {
        "rules": [{
            "name": name,
            "pattern": pattern,
            "positive": [
                {"name": f"positive:{index}", "raw": item.raw, "expected": item.expected}
                for index, item in enumerate(observations)
            ],
            "negative": [
                {"name": f"negative:{index}", "raw": raw, "expected": {}}
                for index, raw in enumerate(negative_evidence or [])
            ],
        }]
    }


def string_captures(captures: dict[str, Any]) -> dict[str, str]:
    return {
        name: value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        for name, value in captures.items()
        if value is not None
    }


def result_from_report(report: dict, raw: str, index: int = 0) -> ParserResult:
    results = report.get("results", [])
    result = results[index] if index < len(results) else {}
    matched = bool(result.get("matched_rules"))
    errors = result.get("errors", [])
    error = None
    if not result.get("ok", False):
        error = (
            ":".join(item.get("error", "error") for item in errors)
            if errors else report.get("error", "native_capture_evaluation_failed")
        )
    return ParserResult(
        ok=bool(result.get("ok")),
        captures=string_captures(result.get("captures", {})),
        residual="" if matched else raw,
        error=error,
        trace=[{"layer": "mp_text", "native": result}],
        failed_layer=None if result.get("ok") else "mp_text",
    )


def execute_pattern(pattern: str, raw: str) -> dict:
    report = mp_native.execute_text_captures(capture_document(pattern, [Observation(raw=raw)]))
    return result_from_report(report, raw).model_dump(mode="json")


def evaluate_observations(
    pattern: str,
    record_schema: list[RecordField],
    observations: list[Observation],
    constraints: list[FieldConstraint] | None = None,
    negative_evidence: list[str] | None = None,
) -> dict:
    report = mp_native.execute_text_captures(
        capture_document(pattern, observations, negative_evidence)
    )
    results = []
    for index, observation in enumerate(observations):
        parsed = result_from_report(report, observation.raw, index)
        validation = grok.validate_result(parsed, record_schema, constraints)
        native_result = report.get("results", [])[index] if index < len(report.get("results", [])) else {}
        issues = list(validation["issues"])
        issues.extend(
            json.dumps(error, ensure_ascii=False)
            for error in native_result.get("errors", [])
        )
        results.append({
            "observation": index + 1,
            "ok": not issues,
            "captures": parsed.captures,
            "residual": parsed.residual,
            "failed_layer": parsed.failed_layer,
            "issues": issues,
        })
    offset = len(observations)
    for index, raw in enumerate(negative_evidence or []):
        native_result = report.get("results", [])[offset + index] if offset + index < len(report.get("results", [])) else {}
        errors = native_result.get("errors", [])
        if not native_result:
            errors = ["missing_native_negative_result"]
        results.append({
            "observation": offset + index + 1,
            "negative": True,
            "ok": not errors,
            "raw": raw,
            "matched_rules": native_result.get("matched_rules", []),
            "issues": errors,
        })
    return {
        "ok": bool(results) and all(item["ok"] for item in results),
        "results": results,
        "native_error": report.get("error"),
    }


def evaluate_candidate(
    pattern: str,
    record_schema: list[RecordField],
    observations: list[Observation],
    baseline: ParserBaseline | None = None,
    constraints: list[FieldConstraint] | None = None,
    negative_evidence: list[str] | None = None,
) -> dict:
    candidate = evaluate_observations(
        pattern, record_schema, observations, constraints, negative_evidence
    )
    result = {"ok": candidate["ok"], "candidate": candidate}
    if baseline:
        if baseline.kind != "mp_text" or not baseline.text_pattern:
            return {
                "ok": False,
                "candidate": candidate,
                "baseline": {"ok": False, "issues": ["baseline_kind_must_be_mp_text"]},
            }
        previous = evaluate_observations(
            baseline.text_pattern,
            baseline.record_schema or record_schema,
            baseline.observations,
            baseline.constraints,
            baseline.negative_evidence,
        )
        result["baseline"] = previous
        result["ok"] = result["ok"] and previous["ok"]
    return result
