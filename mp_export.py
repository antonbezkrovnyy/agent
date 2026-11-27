import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
import uuid

import yaml

import mp_native
from mp_profile import DEFAULT_PROFILE, MpTextTargetProfile, load_profile
from mp_schemas import MpTextExportSpec, MpTextRule
from schemas import MappingField, NormalizationField, TransformOperation


CAPTURE = re.compile(r"(?<![A-Za-z0-9_.])(\$?[A-Za-z_][A-Za-z0-9_.]*)\s*=\s*([A-Z][A-Z0-9_]*)\b")
FUNCTION_CALL = re.compile(r"\b([a-z][a-z0-9_]*)\s*\(")
VRL_KEYWORDS = {"if", "else", "for", "in", "while"}


class LiteralString(str):
    pass


def literal_string_representer(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")


yaml.SafeDumper.add_representer(LiteralString, literal_string_representer)


def issue(path: str, error: str, **details) -> dict[str, Any]:
    return {"path": path, "error": error, **details}


def vrl_literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def field_path(source: str) -> str:
    return f".{vrl_literal('$' + source)}"


def target_path(target: str) -> str:
    return f".{vrl_literal(target.replace('.', '_'))}"


def capture_inventory(pattern: str) -> list[tuple[str, str]]:
    return CAPTURE.findall(pattern)


def validate_condition(condition: str | None, profile: MpTextTargetProfile, path: str) -> list[dict]:
    if condition is None:
        return []
    errors = []
    for function in FUNCTION_CALL.findall(condition):
        if function not in VRL_KEYWORDS and function not in profile.functions:
            errors.append(issue(path, "unsupported_function", function=function))
    return errors


def operation_expression(
    source: str,
    operation: TransformOperation,
    spec: MpTextExportSpec,
    path: str,
    native_timestamp: bool = False,
) -> tuple[list[str], list[dict]]:
    name = operation.op
    params = operation.params
    value = field_path(source)
    lines = []
    errors = []
    allowed = {
        "trim": set(), "null_if": {"values"}, "to_integer": set(), "lowercase": set(),
        "normalize_ip": {"version"}, "validate_range": {"min", "max"},
        "parse_timestamp": {"format", "formats", "default_year", "input_timezone", "output_timezone"},
    }
    unknown = sorted(set(params) - allowed[name])
    if unknown:
        return [], [issue(path, "invalid_operation_params", operation=name, params=unknown)]
    if name == "trim":
        lines.append(f"{value} = strip_whitespace!({value});")
    elif name == "null_if":
        values = params.get("values")
        if not isinstance(values, list):
            errors.append(issue(path, "null_if_requires_values_list"))
        else:
            lines.append(f"{{if mp_bool(mp_in_list({vrl_literal(values)}, {value})) {{ {value} = null }}}};")
    elif name == "to_integer":
        lines.extend([f"{value} = mp_number({value});", f"{{if is_null({value}) {{ abort }}}};"])
    elif name == "lowercase":
        lines.extend([f"{value} = mp_lower({value});", f"{{if is_null({value}) {{ abort }}}};"])
    elif name == "normalize_ip":
        errors.append(issue(
            path,
            "unsupported_capability",
            capability="normalize_ip_canonicalization",
            recovery="Используйте MP IPV4/IPV6 capture и проверьте expected event; canonicalization пока не понижается.",
        ))
    elif name == "validate_range":
        if "min" in params:
            lines.append(f"{{if mp_bool(mp_lt({value}, {vrl_literal(params['min'])})) {{ abort }}}};")
        if "max" in params:
            lines.append(f"{{if mp_bool(mp_gt({value}, {vrl_literal(params['max'])})) {{ abort }}}};")
    elif name == "parse_timestamp":
        if native_timestamp:
            return [], []
        formats = params.get("formats")
        if formats is None and "format" in params:
            formats = [params["format"]]
        if not isinstance(formats, list) or not formats or any(not isinstance(item, str) for item in formats):
            errors.append(issue(path, "parse_timestamp_requires_formats"))
            return [], errors
        if params.get("default_year") is not None:
            errors.append(issue(
                path,
                "unsupported_capability",
                capability="parse_timestamp_default_year",
                recovery="Используйте поддерживаемый DATETIME token либо явный год в источнике.",
            ))
        output_timezone = params.get("output_timezone")
        if output_timezone not in {None, "UTC", "Z", "+00:00"}:
            errors.append(issue(path, "unsupported_output_timezone", timezone=output_timezone))
        timezone = params.get("input_timezone") or spec.source_timezone
        if not timezone:
            errors.append(issue(path, "parse_timestamp_requires_source_timezone"))
        variable = "vrl_tmp_" + re.sub(r"[^a-zA-Z0-9_]", "_", source)
        text_variable = variable + "_text"
        lines.append(f"{variable} = null;")
        formatted_variable = variable + "_formatted"
        raw_variable = variable + "_raw"
        lines.append(
            f"{formatted_variable}, {variable}_format_error = format_timestamp({value}, "
            f"format: {vrl_literal(formats[0])}, timezone: \"UTC\");"
        )
        lines.append(f"{raw_variable} = string!(mp_string({value}));")
        lines.append(
            f"{text_variable} = string!(mp_coalesce([{formatted_variable}, {raw_variable}]));"
        )
        error_variable = variable + "_error"
        for format_string in formats:
            timezone_argument = f", timezone: {vrl_literal(timezone)}" if timezone else ""
            lines.append(
                f"{{if is_null({variable}) {{ {variable}, {error_variable} = parse_timestamp({text_variable}, "
                f"format: {vrl_literal(format_string)}{timezone_argument}) }}}};"
            )
        lines.extend([f"{{if is_null({variable}) {{ abort }}}};", f"{value} = {variable};"])
    return lines, errors


def compile_normalization(
    field: NormalizationField,
    rule: MpTextRule,
    spec: MpTextExportSpec,
    path: str,
) -> tuple[list[str], list[dict]]:
    source = field_path(field.source)
    lines = []
    errors = []
    native_timestamp = any(
        name == "$" + field.source and token.startswith("DATETIME")
        for name, token in capture_inventory(rule.pattern)
    )
    for index, operation in enumerate(field.operations):
        operation_lines, operation_errors = operation_expression(
            field.source,
            operation,
            spec,
            f"{path}.operations.{index}",
            native_timestamp,
        )
        lines.extend(operation_lines)
        errors.extend(operation_errors)
    if field.required:
        lines.append(f"{{if is_null({source}) || mp_string({source}) == \"\" {{ abort }}}};")
    if lines:
        if field.required:
            body = "\n  ".join(lines)
            return [f"{{if exists({source}) {{\n  {body}\n}} else {{ abort }}}};"], errors
        body = "\n  ".join(lines)
        return [f"{{if exists({source}) {{\n  {body}\n}}}};"], errors
    return [], errors


def mapping_condition(mapping: MappingField, path: str) -> tuple[str | None, list[dict]]:
    if mapping.condition is None:
        return None, []
    if set(mapping.condition) != {"format"}:
        return None, [issue(path, "invalid_mapping_condition")]
    format_name = mapping.condition["format"]
    source = f"string!(mp_string({field_path(mapping.source)}))"
    if format_name == "ipv4":
        return f"is_ipv4({source})", []
    if format_name == "ipv6":
        return f"is_ipv6({source})", []
    if format_name == "ip":
        return f"is_ipv4({source}) || is_ipv6({source})", []
    if format_name == "hostname":
        return (
            f"mp_regex_match({source}, r'^[A-Za-z0-9_.\\-]+$') "
            f"&& !is_ipv4({source}) && !is_ipv6({source})",
            [],
        )
    return None, [issue(path, "unsupported_capability", capability=f"mapping_condition_{format_name}")]


def compile_mapping(
    mapping: MappingField,
    rule: MpTextRule,
    spec: MpTextExportSpec,
    profile: MpTextTargetProfile,
    path: str,
    required: bool,
) -> tuple[list[str], list[dict]]:
    errors = []
    target = target_path(mapping.target)
    if mapping.source is None:
        return [f"{target} = {vrl_literal(mapping.constant)};"], errors
    source = field_path(mapping.source)
    value = source
    condition, condition_errors = mapping_condition(mapping, f"{path}.condition")
    errors.extend(condition_errors)
    if mapping.value_map is not None:
        keys = [str(key) for key in mapping.value_map]
        values = list(mapping.value_map.values())
        lines = [f"{{if !mp_bool(mp_in_list({vrl_literal(keys)}, mp_string({source}))) {{ abort }}}};"]
        value = f"switch_hash(mp_string({source}), null, {vrl_literal(keys)}"
        if values:
            value += ", " + ", ".join(vrl_literal(item) for item in values)
        value += ")"
    else:
        lines = []
    if mapping.target in {"time", "start_time"}:
        schema_field = next((field for field in rule.record_schema if field.name == mapping.source), None)
        timestamp_source = bool(
            (schema_field and schema_field.type == "timestamp")
            or any(token.startswith("DATETIME") for name, token in capture_inventory(rule.pattern) if name == "$" + mapping.source)
            or any(
                field.source == mapping.source and any(operation.op == "parse_timestamp" for operation in field.operations)
                for field in rule.normalization
            )
        )
        has_normalized_timestamp = any(
            field.source == mapping.source
            and any(operation.op == "parse_timestamp" for operation in field.operations)
            for field in rule.normalization
        )
        naive_tokens = {
            token for name, token in capture_inventory(rule.pattern)
            if name == "$" + mapping.source and token in {"DATETIME", "DATETIME_SYSLOG"}
        }
        if timestamp_source and spec.source_timezone and naive_tokens and not has_normalized_timestamp:
            variable = "vrl_tmp_" + mapping.target.replace(".", "_")
            lines.append(
                f"{variable} = format_timestamp!({value}, format: \"%Y-%m-%dT%H:%M:%S%.3f\", timezone: \"UTC\");"
            )
            value = (
                f"parse_timestamp!({variable}, format: \"%Y-%m-%dT%H:%M:%S%.3f\", "
                f"timezone: {vrl_literal(spec.source_timezone)})"
            )
        elif not timestamp_source:
            if not spec.source_timezone:
                errors.append(issue(path, "time_mapping_requires_source_timezone"))
            timezone = f", timezone: {vrl_literal(spec.source_timezone)}" if spec.source_timezone else ""
            value = f"parse_timestamp!({value}, format: \"%+\"{timezone})"
    lines.append(f"{target} = {value};")
    body = "\n  ".join(lines)
    presence = f"exists({source})"
    if condition:
        presence += f" && ({condition})"
    if required:
        lines = [f"{{if {presence} {{\n  {body}\n}} else {{ abort }}}};"]
    else:
        lines = [f"{{if {presence} {{\n  {body}\n}}}};"]
    return lines, errors


def validate_rule(
    rule: MpTextRule,
    spec: MpTextExportSpec,
    profile: MpTextTargetProfile,
    path: str,
) -> list[dict]:
    errors = []
    captures = capture_inventory(rule.pattern)
    for index, (_, token) in enumerate(captures):
        if token not in profile.text_tokens:
            errors.append(issue(f"{path}.pattern.captures.{index}", "unsupported_text_token", token=token))
    direct = [name for name, _ in captures if not name.startswith("$")]
    if direct:
        errors.append(issue(f"{path}.pattern", "direct_captures_forbidden_use_temporary_variables", fields=direct))
    capture_names = [name[1:] for name, _ in captures if name.startswith("$")]
    schema_names = [field.name for field in rule.record_schema if field.name != "raw"]
    if set(schema_names) != set(capture_names):
        errors.append(issue(
            f"{path}.record_schema",
            "capture_schema_mismatch",
            missing=sorted(set(capture_names) - set(schema_names)),
            unknown=sorted(set(schema_names) - set(capture_names)),
        ))
    normalization_sources = [field.source for field in rule.normalization]
    if set(normalization_sources) != set(schema_names) or len(normalization_sources) != len(schema_names):
        errors.append(issue(f"{path}.normalization", "normalization_must_describe_every_capture_once"))
    if len(rule.expected_events) != len(rule.evidence):
        errors.append(issue(f"{path}.expected_events", "expected_events_must_align_with_evidence"))
    if rule.native_expected_events is not None and len(rule.native_expected_events) != len(rule.evidence):
        errors.append(issue(f"{path}.native_expected_events", "native_expected_events_must_align_with_evidence"))
    for evidence_index, raw in enumerate(rule.evidence):
        if not any(anchor in raw for anchor in spec.dispatch_contains):
            errors.append(issue(
                f"{path}.evidence.{evidence_index}",
                "dispatch_anchor_does_not_cover_evidence",
                anchors=spec.dispatch_contains,
            ))
    errors.extend(validate_condition(rule.condition, profile, f"{path}.condition"))

    targets = [mapping.target for mapping in rule.mappings]
    duplicates = sorted({target for target in targets if targets.count(target) > 1})
    if duplicates:
        errors.append(issue(f"{path}.mappings", "duplicate_mapping_targets", targets=duplicates))
    flat_targets = {target.replace(".", "_") for target in targets}
    required = set(profile.data["normalizer"]["required_event_fields"])
    missing = sorted(required - flat_targets)
    if missing:
        errors.append(issue(f"{path}.mappings", "missing_required_event_fields", fields=missing))
    enum_values = {}
    for item in profile.data["taxonomy"]["semantic_values"]:
        enum_values.setdefault(item["field"], set()).add(item["value"])
    for index, mapping in enumerate(rule.mappings):
        mapping_path = f"{path}.mappings.{index}"
        target_error = profile.validate_target(mapping.target)
        if target_error:
            errors.append(issue(mapping_path, target_error))
        if mapping.source is not None and mapping.source not in schema_names:
            errors.append(issue(mapping_path, "unknown_mapping_source", source=mapping.source))
        if mapping.target == "id":
            event_id_pattern = profile.data["output"]["event_id_pattern"]
            if not isinstance(mapping.constant, str) or not re.fullmatch(event_id_pattern, mapping.constant):
                errors.append(issue(mapping_path, "invalid_event_id", expected=event_id_pattern))
        allowed = enum_values.get(mapping.target)
        if allowed:
            if mapping.constant is not None and mapping.constant not in allowed:
                errors.append(issue(mapping_path, "invalid_enum_constant", value=mapping.constant))
            if mapping.value_map:
                unknown_values = [value for value in mapping.value_map.values() if value not in allowed]
                if unknown_values:
                    errors.append(issue(mapping_path, "invalid_enum_mapped_values", values=unknown_values))
    return errors


def compile_spec(
    spec: MpTextExportSpec,
    profile: MpTextTargetProfile = load_profile(),
) -> dict[str, Any]:
    errors = []
    rule_keys = [rule.rule_key for rule in spec.rules]
    if len(set(rule_keys)) != len(rule_keys):
        errors.append(issue("rules", "duplicate_rule_keys"))
    for index, anchor in enumerate(spec.dispatch_contains):
        if not anchor or "\n" in anchor or "\r" in anchor:
            errors.append(issue(f"dispatch_contains.{index}", "invalid_dispatch_anchor"))
    documents = []
    fixtures = {"positive": [], "negative": []}
    for rule_index, rule in enumerate(spec.rules):
        path = f"rules.{rule_index}"
        errors.extend(validate_rule(rule, spec, profile, path))
        body = []
        for field_index, field in enumerate(rule.normalization):
            lines, field_errors = compile_normalization(field, rule, spec, f"{path}.normalization.{field_index}")
            body.extend(lines)
            errors.extend(field_errors)
        required_targets = set(profile.data["normalizer"]["required_event_fields"])
        for mapping_index, mapping in enumerate(rule.mappings):
            lines, mapping_errors = compile_mapping(
                mapping,
                rule,
                spec,
                profile,
                f"{path}.mappings.{mapping_index}",
                mapping.target.replace(".", "_") in required_targets,
            )
            body.extend(lines)
            errors.extend(mapping_errors)
        name = profile.data["output"]["rule_name"].format(source=spec.source, rule_key=rule.rule_key)
        documents.append({
            "name": name,
            "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"logicore:mp-text:{spec.source}:{rule.rule_key}")),
            "subformulas": None,
            "rule_info": {
                "grok": {"kind": "TEXT", "pattern": rule.pattern},
                "condition": rule.condition,
                "body": LiteralString("\n".join(body) + "\n"),
            },
        })
        required_schema = {field.name for field in rule.record_schema if field.required}
        required_fixture_fields = set(profile.data["normalizer"]["required_event_fields"])
        required_fixture_fields.update(
            mapping.target.replace(".", "_")
            for mapping in rule.mappings
            if mapping.condition is None and (mapping.source is None or mapping.source in required_schema)
        )
        native_expected = rule.native_expected_events or rule.expected_events
        for evidence_index, (raw, expected) in enumerate(zip(rule.evidence, native_expected, strict=True)):
            fixtures["positive"].append({
                "name": f"{rule.rule_key}:{evidence_index}",
                "event": {"message": raw, "mime": "TEXT"},
                "required_fields": sorted(required_fixture_fields),
                "expected": expected,
                "expected_saturn": rule.expected_events[evidence_index],
                "unexpected_fields": ["$" + field.name for field in rule.record_schema if field.name != "raw"],
            })
        for evidence_index, raw in enumerate(rule.negative_evidence):
            fixtures["negative"].append({
                "name": f"{rule.rule_key}:negative:{evidence_index}",
                "event": {"message": raw, "mime": "TEXT"},
                "status": "unparsed",
            })
    if errors:
        return {"ok": False, "error": "mp_compilation_failed", "issues": errors}
    yaml_text = yaml.safe_dump(
        {"appendix": LiteralString(profile.data["normalizer"]["appendix"]), "rules": documents},
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )
    conf_text = compile_conf(spec, profile)
    canonical_spec = json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "ok": True,
        "yaml": yaml_text,
        "conf": conf_text,
        "fixtures": fixtures,
        "spec_hash": hashlib.sha256(canonical_spec.encode("utf-8")).hexdigest(),
        "rules": len(documents),
        "positive_fixtures": len(fixtures["positive"]),
        "negative_fixtures": len(fixtures["negative"]),
    }


def logstash_literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def compile_conf(spec: MpTextExportSpec, profile: MpTextTargetProfile) -> str:
    source = spec.source
    output = profile.data["output"]
    normalizer = profile.data["normalizer"]
    condition = " or ".join(f"{logstash_literal(anchor)} in [message]" for anchor in spec.dispatch_contains)
    success_tag = output["conf_success_tag"].format(source=source)
    failure_tag = output["conf_failure_tag"].format(source=source)
    unparsed_tag = output["conf_unparsed_tag"].format(source=source)
    rules_path = output["rules_path"].format(source=source)
    return f"""filter {{
  if [message] and ({condition}) {{
    mp_normalizer {{
      id => \"parse_{source}_specific\"
      rules_filepath => \"/usr/share/logstash/conf.d/{rules_path.removeprefix('pipeline/')}\"
      body_field => \"{normalizer['body_field']}\"
      mime_type_field => \"{normalizer['mime_type_field']}\"
      tag_on_failure => \"{failure_tag}\"
      tag_on_unparsed => \"{unparsed_tag}\"
      pass_fields => {{
        \"recv_ipv4\" => \"recv_ipv4\"
        \"recv_ipv6\" => \"recv_ipv6\"
      }}
    }}
    if \"{unparsed_tag}\" in [tags] or \"{failure_tag}\" in [tags] {{
      mutate {{
        remove_tag => [\"{unparsed_tag}\", \"{failure_tag}\"]
      }}
    }} else {{
      mutate {{
        add_tag => [\"{success_tag}\"]
        remove_tag => [\"unparsed_autonormalizer\"]
      }}
    }}
  }}
}}
"""


def export_package(
    spec: MpTextExportSpec,
    output_root: Path,
    profile_path: Path = DEFAULT_PROFILE,
    native: bool = True,
    mpsiem: str | Path = mp_native.DEFAULT_MPSIEM,
    native_mode: str = "docker",
) -> dict[str, Any]:
    profile = load_profile(profile_path)
    compiled = compile_spec(spec, profile)
    if not compiled["ok"]:
        return compiled
    rules_path = output_root / profile.data["output"]["rules_path"].format(source=spec.source)
    conf_path = output_root / profile.data["output"]["conf_path"].format(source=spec.source)
    fixtures_path = output_root / "fixtures" / spec.source / "native_fixtures.json"
    report_path = output_root / "fixtures" / spec.source / "validation_report.json"
    package = {
        "schema_version": 1,
        "profile": profile.name,
        "source": spec.source,
        "revision": spec.revision,
        "spec_hash": compiled["spec_hash"],
        "rules_path": str(rules_path),
        "conf_path": str(conf_path),
        "fixtures_path": str(fixtures_path),
        "report_path": str(report_path),
        "rules": compiled["rules"],
        "positive_fixtures": compiled["positive_fixtures"],
        "negative_fixtures": compiled["negative_fixtures"],
        "assumptions": spec.assumptions,
        "native": {"ok": False, "status": "not_run"},
    }
    if native:
        report = mp_native.validate_rules_from_text(
            compiled["yaml"], compiled["fixtures"], mpsiem=mpsiem, mode=native_mode
        )
        package["native"] = report
        package["ok"] = bool(report.get("ok"))
        if not package["ok"]:
            package["error"] = "native_validation_failed"
    else:
        package["native"] = {"ok": False, "status": "skipped"}
        package["ok"] = False
        package["error"] = "native_validation_required"
    for path, content in (
        (rules_path, compiled["yaml"]),
        (conf_path, compiled["conf"]),
        (fixtures_path, json.dumps(compiled["fixtures"], ensure_ascii=False, indent=2) + "\n"),
        (report_path, json.dumps(package, ensure_ascii=False, indent=2) + "\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return package


def native_expected_event(event: dict, profile: MpTextTargetProfile) -> dict:
    definitions = {
        item["name"].replace(".", "_"): item["type"]
        for item in profile.data["taxonomy"]["semantic_field_definitions"]
    }
    result = {}
    for name, value in event.items():
        flat_name = name.replace(".", "_")
        if value is not None and definitions.get(flat_name) == "MACAddress":
            value = str(value).upper()
        if value is not None and definitions.get(flat_name) == "DateTime":
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            value = parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        result[flat_name] = value
    return result


def dispatch_anchors(pattern: str, evidence: list[str]) -> list[str]:
    without_assignments = re.sub(
        r"\$[A-Za-z_][A-Za-z0-9_.]*\s*=\s*(?:UNTIL\([^\n{}]*\)|[A-Z][A-Z0-9_]*)",
        " ",
        pattern,
    )
    quoted = []
    for match in re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', without_assignments):
        literal = match.group(0)[1:-1]
        if len(literal) >= 4 and any(char.isalnum() for char in literal):
            quoted.append(literal)
    bare = without_assignments.translate(str.maketrans({char: "\0" for char in '"{}|?+*[]'}))
    for part in bare.split("\0"):
        anchor = re.sub(r"\s+", " ", part).strip().strip("()[]").strip()
        if len(anchor) >= 4 and any(char.isalnum() for char in anchor):
            quoted.append(anchor)
    return sorted({
        anchor for anchor in quoted
        if evidence and all(anchor in raw for raw in evidence)
    })


def spec_from_revisions(
    source: str,
    family_id: str,
    mapping_revision: int | None = None,
    dispatch_contains: list[str] | None = None,
    revision: int | None = None,
    profile_path: Path = DEFAULT_PROFILE,
) -> MpTextExportSpec:
    import mapping
    import mp_text
    import storage

    saved_mapping = storage.get_mapping(family_id, mapping_revision)
    if not saved_mapping["ok"]:
        raise ValueError(saved_mapping["message"])
    mapping_data = saved_mapping["mapping"]
    family_data = storage.get_family(family_id, mapping_data["parser_revision"])
    if not family_data["ok"]:
        raise ValueError(family_data["message"])
    family = family_data["family"]
    if family.get("target") != "mp_text" or not family.get("text_pattern"):
        raise ValueError("Family должна иметь target=mp_text и text_pattern")
    normalization = storage.get_normalization(
        family_id,
        mapping_data["normalization_revision"],
        mapping_data["parser_revision"],
    )
    if not normalization["ok"]:
        raise ValueError(normalization["message"])
    evidence = mapping_data["evidence"]
    evaluated = mapping.evaluate_mappings(
        family_id,
        mapping_data["parser_revision"],
        mapping_data["normalization_revision"],
        [MappingField.model_validate(item) for item in mapping_data["mappings"]],
        evidence,
        mapping_data["source_timezone"],
    )
    if not evaluated["ok"]:
        raise ValueError(f"Сохранённый mapping не проходит evidence: {evaluated}")
    profile = load_profile(profile_path)
    expected = [
        native_expected_event(result["event"], profile)
        for result in evaluated["results"]
    ]
    native_expected = [dict(event) for event in expected]
    captures_by_rule = capture_inventory(family["text_pattern"])
    for index, raw in enumerate(evidence):
        native_capture = mp_text.execute_pattern(family["text_pattern"], raw)
        if not native_capture.get("ok"):
            raise ValueError(f"Native parser не повторил evidence: {native_capture}")
        for item in mapping_data["mappings"]:
            if item.get("target") not in {"time", "start_time"}:
                continue
            capture_source = item.get("source")
            is_native_datetime = any(
                name == "$" + capture_source and token.startswith("DATETIME")
                for name, token in captures_by_rule
            )
            if is_native_datetime and capture_source in native_capture["captures"]:
                native_expected[index] = {
                    **native_expected[index],
                    item["target"]: native_expected_event({
                        item["target"]: native_capture["captures"][capture_source]
                    }, profile)[item["target"]],
                }
    anchors = dispatch_contains or dispatch_anchors(family["text_pattern"], evidence)
    if not anchors:
        raise ValueError("Не удалось вывести dispatch anchors; задайте dispatch_contains явно")
    rule_key = re.sub(r"[^a-z0-9_]+", "_", family_id.lower()).strip("_")
    if not rule_key or not rule_key[0].isalpha():
        rule_key = "family_" + rule_key
    rule = MpTextRule(
        rule_key=rule_key,
        description=family["family_description"].get("summary", family_id),
        pattern=family["text_pattern"],
        condition=None,
        record_schema=family["record_schema"],
        normalization=normalization["normalization"]["fields"],
        mappings=mapping_data["mappings"],
        evidence=evidence,
        expected_events=expected,
        native_expected_events=native_expected,
        negative_evidence=family.get("negative_evidence", []),
    )
    return MpTextExportSpec(
        source=source,
        revision=revision or mapping_data["revision"],
        dispatch_contains=anchors,
        source_timezone=mapping_data["source_timezone"],
        rules=[rule],
        assumptions=[],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile MP TEXT candidate into YAML, CONF and native fixtures.")
    parser.add_argument("spec", nargs="?", type=Path)
    parser.add_argument("--family-id", help="Собрать candidate из существующих parser/normalization/mapping revisions.")
    parser.add_argument("--mapping-revision", type=int)
    parser.add_argument("--source", help="Logicore source name для registry export.")
    parser.add_argument("--database", help="RESEARCH_DB с исходными revisions.")
    parser.add_argument("--dispatch-contains", action="append", help="Явный CONF anchor; можно повторять.")
    parser.add_argument("--export-revision", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--mpsiem", type=Path, default=mp_native.DEFAULT_MPSIEM)
    parser.add_argument("--native-mode", choices=["docker", "local"], default="docker")
    parser.add_argument("--no-native", action="store_true", help="Generate draft files; result remains not ready.")
    args = parser.parse_args()
    if args.family_id:
        if not args.source:
            parser.error("--family-id требует --source")
        if args.database:
            os.environ["RESEARCH_DB"] = args.database
        spec = spec_from_revisions(
            args.source,
            args.family_id,
            args.mapping_revision,
            args.dispatch_contains,
            args.export_revision,
            args.profile,
        )
    elif args.spec:
        spec = MpTextExportSpec.model_validate_json(args.spec.read_text(encoding="utf-8"))
    else:
        parser.error("Укажите SPEC.json или --family-id")
    result = export_package(
        spec, args.output, args.profile, not args.no_native, args.mpsiem, args.native_mode
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
