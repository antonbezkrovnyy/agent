from datetime import datetime, timedelta, timezone
import ipaddress
import re
from typing import Any
from zoneinfo import ZoneInfo

import grok
import runtime
import storage
from schemas import (
    FieldConstraint,
    NormalizationField,
    ParserResult,
    RecordField,
    TransformOperation,
)


OPERATION_PARAMS = {
    "trim": set(),
    "null_if": {"values"},
    "to_integer": set(),
    "lowercase": set(),
    "normalize_ip": {"version"},
    "validate_range": {"min", "max"},
    "parse_timestamp": {"format", "formats", "default_year", "input_timezone", "output_timezone"},
}


def parse_timezone(value: str):
    if value in {"UTC", "Z", "+00:00", "-00:00"}:
        return timezone.utc
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value)
    if match:
        hours, minutes = int(match.group(2)), int(match.group(3))
        if hours > 23 or minutes > 59:
            raise ValueError(f"invalid_timezone:{value}")
        delta = timedelta(hours=hours, minutes=minutes)
        return timezone(-delta if match.group(1) == "-" else delta)
    return ZoneInfo(value)


def timestamp_value(value: Any, params: dict) -> str:
    formats = params.get("formats")
    if formats is None and "format" in params:
        formats = [params["format"]]
    if not isinstance(formats, list) or not formats or any(not isinstance(item, str) for item in formats):
        raise ValueError("parse_timestamp: требуется непустой formats или format")
    errors = []
    for format_string in formats:
        try:
            parsed = datetime.strptime(str(value), format_string)
        except ValueError as error:
            errors.append(str(error))
            continue
        if "%Y" not in format_string and "%y" not in format_string:
            default_year = params.get("default_year")
            if not isinstance(default_year, int):
                raise ValueError("parse_timestamp: в формате нет года; задайте default_year явно")
            parsed = parsed.replace(year=default_year)
        input_name = params.get("input_timezone")
        output_name = params.get("output_timezone")
        if input_name is not None and not isinstance(input_name, str):
            raise ValueError("parse_timestamp: input_timezone должен быть строкой")
        if output_name is not None and not isinstance(output_name, str):
            raise ValueError("parse_timestamp: output_timezone должен быть строкой")
        if input_name:
            parsed = parsed.replace(tzinfo=parse_timezone(input_name))
        if output_name:
            if parsed.tzinfo is None:
                raise ValueError("parse_timestamp: output_timezone требует input_timezone")
            parsed = parsed.astimezone(parse_timezone(output_name))
        return parsed.isoformat()
    raise ValueError(f"parse_timestamp: формат не совпал: {errors[-1] if errors else 'unknown'}")


def apply_transform(operation: TransformOperation, value: Any) -> dict:
    name = operation.op
    params = operation.params
    unknown = set(params) - OPERATION_PARAMS[name]
    if unknown:
        return {"ok": False, "error": f"invalid_params:{name}:{sorted(unknown)}:allowed={sorted(OPERATION_PARAMS[name])}"}
    if value is None:
        return {"ok": True, "result": None, "error": None}
    try:
        if name == "trim":
            result = str(value).strip()
        elif name == "null_if":
            values = params.get("values")
            if not isinstance(values, list):
                raise ValueError("null_if: требуется список values")
            result = None if value in values else value
        elif name == "to_integer":
            if isinstance(value, bool):
                raise ValueError("to_integer: boolean не является integer")
            result = int(value)
        elif name == "lowercase":
            result = str(value).lower()
        elif name == "normalize_ip":
            address = ipaddress.ip_address(str(value))
            version = params.get("version", "any")
            if version not in {"any", "ipv4", "ipv6"}:
                raise ValueError("normalize_ip: version должен быть any, ipv4 или ipv6")
            if version != "any" and address.version != int(version[-1]):
                raise ValueError(f"normalize_ip: ожидался {version}, получен IPv{address.version}")
            result = address.compressed
        elif name == "validate_range":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError("validate_range: сначала примените числовое преобразование")
            minimum = params.get("min")
            maximum = params.get("max")
            if minimum is not None and value < minimum:
                raise ValueError(f"validate_range: {value} < {minimum}")
            if maximum is not None and value > maximum:
                raise ValueError(f"validate_range: {value} > {maximum}")
            result = value
        else:
            result = timestamp_value(value, params)
        return {"ok": True, "result": result, "error": None}
    except (ValueError, TypeError) as error:
        return {"ok": False, "error": str(error)}


def normalize_captures(captures: dict[str, str], fields: list[NormalizationField]) -> dict:
    normalized = dict(captures)
    errors = []
    trace = []
    for field in fields:
        if field.source not in captures:
            if field.required:
                errors.append({"source": field.source, "error": "missing_required_source"})
            trace.append({"source": field.source, "status": "missing"})
            continue
        value = captures[field.source]
        failed = False
        for operation in field.operations:
            result = apply_transform(operation, value)
            trace.append({
                "source": field.source,
                "op": operation.op,
                "status": "ok" if result["ok"] else "error",
                "input": value,
                "output": result.get("result"),
                "error": result["error"],
            })
            if not result["ok"]:
                errors.append({"source": field.source, "op": operation.op, "error": result["error"]})
                failed = True
                break
            value = result["result"]
        if failed:
            continue
        if field.required and (value is None or value == ""):
            errors.append({"source": field.source, "error": "missing_required_after_transform"})
        normalized[field.source] = value
    return {
        "ok": not errors,
        "normalized": normalized,
        "errors": errors,
        "trace": trace,
    }


def definition_issues(family: dict, fields: list[NormalizationField], evidence: list[str]) -> list[str]:
    issues = []
    expected = {field["name"] for field in family["record_schema"] if field["name"] != "raw"}
    sources = [field.source for field in fields]
    duplicates = sorted({source for source in sources if sources.count(source) > 1})
    if duplicates:
        issues.append(f"duplicate_sources:{duplicates}")
    missing = sorted(expected - set(sources))
    if missing:
        issues.append(f"undescribed_record_fields:{missing}")
    unknown = sorted(set(sources) - expected)
    if unknown:
        issues.append(f"unknown_sources:{unknown}:expected={sorted(expected)}")
    if not evidence:
        issues.append("empty_evidence")
    return issues


def evaluate_fields(family: dict, fields: list[NormalizationField], evidence: list[str]) -> dict:
    issues = definition_issues(family, fields, evidence)
    if issues:
        return {"ok": False, "issues": issues, "results": []}
    record_schema = [RecordField.model_validate(field) for field in family["record_schema"]]
    constraints = [FieldConstraint.model_validate(rule) for rule in family["constraints"]]
    results = []
    for index, raw in enumerate(evidence, 1):
        parser_result = runtime.execute_family(family, raw)
        parser_validation = grok.validate_result(
            ParserResult.model_validate(parser_result),
            record_schema,
            constraints,
        )
        if not parser_validation["ok"]:
            results.append({
                "observation": index,
                "ok": False,
                "parse_ok": False,
                "issues": parser_validation["issues"],
            })
            continue
        normalization = normalize_captures(parser_result["captures"], fields)
        results.append({
            "observation": index,
            "ok": normalization["ok"],
            "parse_ok": True,
            "normalization_ok": normalization["ok"],
            "captures": parser_result["captures"],
            **normalization,
        })
    return {"ok": bool(results) and all(result["ok"] for result in results), "results": results}


def preview_normalization(
    family_id: str,
    parser_revision: int,
    fields: list[NormalizationField],
    raw: str,
) -> dict:
    saved = storage.get_family(family_id, parser_revision)
    if not saved["ok"]:
        return saved
    evaluation = evaluate_fields(saved["family"], fields, [raw])
    return {"ok": evaluation["ok"], "evaluation": evaluation}


def evaluate_normalization(
    family_id: str,
    parser_revision: int,
    fields: list[NormalizationField],
    evidence: list[str],
) -> dict:
    saved = storage.get_family(family_id, parser_revision)
    if not saved["ok"]:
        return saved
    return evaluate_fields(saved["family"], fields, evidence)


def normalize_with_latest(family_id: str, parser_revision: int, raw: str) -> dict:
    saved = storage.get_family(family_id, parser_revision)
    if not saved["ok"]:
        return saved
    family = saved["family"]
    parser_result = runtime.execute_family(family, raw)
    record_schema = [RecordField.model_validate(field) for field in family["record_schema"]]
    constraints = [FieldConstraint.model_validate(rule) for rule in family["constraints"]]
    parser_validation = grok.validate_result(
        ParserResult.model_validate(parser_result),
        record_schema,
        constraints,
    )
    if not parser_validation["ok"]:
        return {
            "ok": False,
            "parse_ok": False,
            "normalization_ok": None,
            "status": "parse_failed",
            "errors": parser_validation["issues"],
        }
    normalized_saved = storage.get_normalization(family_id, parser_revision=parser_revision)
    if not normalized_saved["ok"]:
        return {
            "ok": True,
            "parse_ok": True,
            "normalization_ok": None,
            "status": "not_configured",
            "captures": parser_result["captures"],
            "normalized": None,
            "errors": [],
        }
    fields = [NormalizationField.model_validate(field) for field in normalized_saved["normalization"]["fields"]]
    result = normalize_captures(parser_result["captures"], fields)
    return {
        "ok": result["ok"],
        "parse_ok": True,
        "normalization_ok": result["ok"],
        "status": "normalized" if result["ok"] else "normalization_failed",
        "normalization_revision": normalized_saved["normalization"]["revision"],
        "captures": parser_result["captures"],
        **result,
    }
