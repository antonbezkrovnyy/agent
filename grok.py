import ipaddress

import regex

from schemas import GrokLayer, Observation, ParserBaseline, ParserResult, RecordField


GROK_PATTERNS = {}
GROK_REFERENCE = regex.compile(r"%\{([A-Za-z0-9_]+)(?::([A-Za-z_][A-Za-z0-9_]*))?\}")


def compile_grok(pattern: str, patterns: dict[str, str]) -> regex.Pattern:
    definitions = {**GROK_PATTERNS, **patterns}
    expanded = pattern
    for _ in range(20):
        changed = False

        def replace(match: regex.Match) -> str:
            nonlocal changed
            name, capture = match.groups()
            if name not in definitions:
                raise ValueError(
                    f"unknown_grok_pattern:{name}; добавьте его в patterns или используйте встроенное имя"
                )
            changed = True
            body = definitions[name]
            return f"(?<{capture}>{body})" if capture else f"(?:{body})"

        expanded = GROK_REFERENCE.sub(replace, expanded)
        if not changed:
            try:
                return regex.compile(expanded)
            except regex.error as error:
                raise ValueError(f"invalid_grok_regex:{error}") from error
    raise ValueError("grok_reference_depth_exceeded; проверьте циклические ссылки в patterns")


def run_grok(pattern: str, value: str, patterns: dict[str, str]) -> dict:
    try:
        compiled = compile_grok(pattern, patterns)
        match = compiled.fullmatch(value, timeout=1)
    except Exception as error:
        return {"ok": False, "captures": {}, "matched": "", "residual": value, "error": str(error)}
    if not match:
        return {
            "ok": False,
            "captures": {},
            "matched": "",
            "residual": value,
            "error": "no_match; pattern должен совпасть со всем входом слоя",
        }
    return {
        "ok": True,
        "captures": {key: item for key, item in match.groupdict().items() if item is not None},
        "matched": match.group(0),
        "residual": "",
        "error": None,
    }


def execute_pattern(pattern: str, input_value: str, patterns: dict[str, str]) -> dict:
    if pattern in GROK_PATTERNS or pattern in patterns:
        pattern = f"%{{{pattern}}}"
    return run_grok(pattern, input_value, patterns)


def execute_parser(layers: list[GrokLayer], patterns: dict[str, str], raw: str) -> dict:
    if not layers:
        return {
            "ok": False,
            "captures": {},
            "trace": [],
            "failed_layer": None,
            "residual": raw,
            "error": "invalid_arguments: layers должен содержать хотя бы один слой",
        }
    captures = {}
    trace = []
    value = raw
    for index, layer in enumerate(layers):
        expected_source = "raw" if index == 0 else layers[index - 1].name
        if index == 0 and layer.source != "raw":
            return {
                "ok": False,
                "captures": captures,
                "trace": trace,
                "failed_layer": layer.name,
                "residual": value,
                "error": f"invalid_source:{layer.source}; первый слой должен иметь source=raw",
            }
        if index > 0 and layer.source not in captures:
            return {
                "ok": False,
                "captures": captures,
                "trace": trace,
                "failed_layer": layer.name,
                "residual": value,
                "error": (
                    f"missing_source_capture:{layer.source}; source должен быть capture "
                    f"предыдущего слоя {expected_source}, доступны: {sorted(captures)}"
                ),
            }
        result = run_grok(layer.pattern, value, patterns)
        trace.append({"layer": layer.name, "input": value, **result})
        if not result["ok"]:
            return {
                "ok": False,
                "captures": captures,
                "trace": trace,
                "failed_layer": layer.name,
                "residual": value,
                "error": result["error"],
            }
        captures.update(result["captures"])
        if index == len(layers) - 1:
            value = result["residual"]
            break
        next_source = layers[index + 1].source
        if next_source not in result["captures"]:
            return {
                "ok": False,
                "captures": captures,
                "trace": trace,
                "failed_layer": layers[index + 1].name,
                "residual": value,
                "error": (
                    f"missing_source_capture:{next_source}; предыдущий слой {layer.name} "
                    f"вернул captures: {sorted(result['captures'])}"
                ),
            }
        value = result["captures"][next_source]
    return {
        "ok": not value,
        "captures": captures,
        "trace": trace,
        "failed_layer": None if not value else layers[-1].name,
        "residual": value,
        "error": None if not value else "nonempty_residual",
    }


def validate_result(parser_result: ParserResult, record_schema: list[RecordField]) -> dict:
    issues = []
    captures = parser_result.captures
    if not parser_result.ok:
        issues.append(f"parser:{parser_result.error or 'failed'}")
    for field in record_schema:
        name = field.name
        if name in {"raw", "residual"}:
            continue
        value = captures.get(name)
        if field.required and (value is None or value == ""):
            issues.append(f"missing_required_field:{name}")
            continue
        if value is None:
            continue
        field_type = field.type.lower()
        if "int" in field_type:
            try:
                int(value)
            except (TypeError, ValueError):
                issues.append(f"invalid_integer:{name}:actual={value}")
        if field_type.startswith("ip") or "ipv4" in field_type:
            try:
                ipaddress.ip_address(value)
            except ValueError:
                issues.append(f"invalid_ip:{name}:actual={value}")
    if parser_result.residual:
        issues.append("nonempty_residual")
    return {"ok": not issues, "issues": issues, "captures": captures}


def evaluate_observations(
    layers: list[GrokLayer],
    patterns: dict[str, str],
    record_schema: list[RecordField],
    observations: list[Observation],
) -> dict:
    results = []
    for index, observation in enumerate(observations, 1):
        if not observation.raw:
            results.append({
                "observation": index,
                "ok": False,
                "issues": ["invalid_observation: raw не должен быть пустой строкой"],
            })
            continue
        parser_result = ParserResult.model_validate(execute_parser(layers, patterns, observation.raw))
        validation = validate_result(parser_result, record_schema)
        issues = list(validation["issues"])
        for name, expected in observation.expected.items():
            actual = parser_result.captures.get(name)
            if actual != str(expected):
                issues.append(f"capture_mismatch:{name}:expected={expected}:actual={actual}")
        results.append({
            "observation": index,
            "ok": not issues,
            "captures": parser_result.captures,
            "residual": parser_result.residual,
            "failed_layer": parser_result.failed_layer,
            "issues": issues,
        })
    return {"ok": bool(results) and all(item["ok"] for item in results), "results": results}


def evaluate_candidate(
    layers: list[GrokLayer],
    patterns: dict[str, str],
    record_schema: list[RecordField],
    observations: list[Observation],
    baseline: ParserBaseline | None,
) -> dict:
    candidate = evaluate_observations(layers, patterns, record_schema, observations)
    result = {"ok": candidate["ok"], "candidate": candidate}
    if baseline:
        previous = evaluate_observations(
            baseline.layers,
            baseline.patterns,
            baseline.record_schema or record_schema,
            baseline.observations,
        )
        result["baseline"] = previous
        result["ok"] = result["ok"] and previous["ok"]
    return result
