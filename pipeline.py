import csv
import re
from typing import Any

import grok
from schemas import (
    FieldConstraint,
    Observation,
    ParserBaseline,
    ParserResult,
    PipelineStage,
    RecordField,
)


EXTENSION_KEY_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)=")
CEF_HEADER_FIELDS = (
    "device_vendor",
    "device_product",
    "device_version",
    "signature_id",
    "name",
    "severity",
)


def failure(raw: str, error: str) -> dict:
    return {"ok": False, "fields": {}, "residual": raw, "error": error}


def split_cef_header(raw: str) -> list[str]:
    parts = []
    current = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw):
            current.extend((char, raw[index + 1]))
            index += 2
            continue
        if char == "|":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return parts


def cef_unescape(value: str) -> str:
    escapes = {"n": "\n", "r": "\r", "=": "=", "\\": "\\", "|": "|", '"': '"'}
    result = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            escaped = value[index + 1]
            if escaped in escapes:
                result.append(escapes[escaped])
            else:
                result.extend(("\\", escaped))
            index += 2
            continue
        result.append(value[index])
        index += 1
    return "".join(result)


def parse_cef_extension(extension: str) -> dict[str, str]:
    fields = {}
    position = 0
    while position < len(extension):
        while position < len(extension) and extension[position].isspace():
            position += 1
        if position == len(extension):
            break
        key_match = EXTENSION_KEY_RE.match(extension, position)
        if not key_match:
            raise ValueError(f"invalid_extension_key_at:{position}")
        key = key_match.group(1)
        if key in fields:
            raise ValueError(f"duplicate_extension_key:{key}")
        position = key_match.end()
        if position < len(extension) and extension[position] == '"':
            position += 1
            value_start = position
            while position < len(extension):
                if extension[position] == "\\" and position + 1 < len(extension):
                    position += 2
                    continue
                if extension[position] == '"':
                    break
                position += 1
            if position == len(extension):
                raise ValueError(f"unterminated_quoted_value:{key}")
            value = extension[value_start:position]
            position += 1
            if position < len(extension) and not extension[position].isspace():
                raise ValueError(f"invalid_character_after_quoted_value:{key}:{extension[position]!r}")
        else:
            value_start = position
            value_end = len(extension)
            next_key = len(extension)
            while position < len(extension):
                if extension[position] == "\\" and position + 1 < len(extension):
                    position += 2
                    continue
                if extension[position].isspace():
                    boundary = position
                    while position < len(extension) and extension[position].isspace():
                        position += 1
                    if EXTENSION_KEY_RE.match(extension, position):
                        value_end = boundary
                        next_key = position
                        break
                    continue
                position += 1
            value = extension[value_start:value_end]
            position = next_key
        fields[key] = cef_unescape(value)
    return fields


def parse_cef(raw: str) -> dict:
    if not raw.startswith("CEF:"):
        return failure(raw, "invalid_cef_prefix: ожидается CEF:<version>")
    parts = split_cef_header(raw)
    if len(parts) != 8:
        return failure(raw, f"invalid_cef_header:expected=7_unescaped_separators:actual={len(parts) - 1}")
    version = parts[0][4:]
    if not version:
        return failure(raw, "invalid_cef_version: пустая версия")
    fields = {"cef_version": cef_unescape(version)}
    for name, value in zip(CEF_HEADER_FIELDS, parts[1:7], strict=True):
        fields[name] = cef_unescape(value)
    try:
        extension = parse_cef_extension(parts[7])
    except ValueError as error:
        return failure(raw, f"invalid_cef_extension:{error}")
    overlap = fields.keys() & extension.keys()
    if overlap:
        return failure(raw, f"cef_field_collision:{sorted(overlap)}")
    fields.update(extension)
    return {"ok": True, "fields": fields, "residual": "", "error": None}


def delimited_params(params: dict[str, Any], allowed: set[str]) -> str | None:
    unknown = set(params) - allowed
    if unknown:
        return f"unsupported_params:{sorted(unknown)}:allowed={sorted(allowed)}"
    for name in ("delimiter", "pair_separator"):
        if name in params and (not isinstance(params[name], str) or len(params[name]) != 1):
            return f"invalid_param:{name}:ожидается один символ"
    for name in ("quotechar", "escapechar"):
        if name in params and params[name] is not None and (
            not isinstance(params[name], str) or len(params[name]) != 1
        ):
            return f"invalid_param:{name}:ожидается один символ или null"
    for name in ("label_separator", "key_value_separator"):
        if name in params and params[name] is not None and (
            not isinstance(params[name], str) or not params[name]
        ):
            return f"invalid_param:{name}:ожидается непустая строка или null"
    if "positional_prefix" in params and not isinstance(params["positional_prefix"], str):
        return "invalid_param:positional_prefix:ожидается строка"
    for name in (
        "doublequote",
        "skipinitialspace",
        "strict",
        "strip_keys",
        "strip_values",
        "allow_positionals",
        "allow_empty_keys",
        "allow_empty_fields",
        "allow_empty_values",
    ):
        if name in params and not isinstance(params[name], bool):
            return f"invalid_param:{name}:ожидается boolean"
    for name in ("expected_columns", "expected_pairs"):
        if name in params and (
            not isinstance(params[name], int) or isinstance(params[name], bool) or params[name] < 1
        ):
            return f"invalid_param:{name}:ожидается целое число >= 1"
    if "duplicate_policy" in params and (
        not isinstance(params["duplicate_policy"], str)
        or params["duplicate_policy"] not in {"error", "first", "last"}
    ):
        return "invalid_param:duplicate_policy:ожидается error, first или last"
    if "positional_names" in params:
        names = params["positional_names"]
        if not isinstance(names, dict):
            return "invalid_param:positional_names:ожидается объект {\"1\": \"field_name\"}"
        for index, field_name in names.items():
            if not isinstance(index, str) or not index.isdigit() or int(index) < 1:
                return "invalid_param:positional_names:ключи должны быть номерами колонок начиная с 1"
            if not isinstance(field_name, str) or not field_name:
                return "invalid_param:positional_names:имена полей должны быть непустыми строками"
    if "field_names" in params:
        names = params["field_names"]
        if not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names):
            return "invalid_param:field_names:ожидается список непустых строк"
    return None


def read_delimited_row(raw: str, delimiter: str, params: dict[str, Any]) -> list[str]:
    quotechar = params.get("quotechar", '"')
    rows = list(csv.reader(
        [raw],
        delimiter=delimiter,
        quotechar=quotechar,
        escapechar=params.get("escapechar"),
        doublequote=params.get("doublequote", True),
        skipinitialspace=params.get("skipinitialspace", False),
        strict=params.get("strict", True),
        quoting=csv.QUOTE_NONE if quotechar is None else csv.QUOTE_MINIMAL,
    ))
    if len(rows) != 1:
        raise ValueError(f"expected_one_record:actual={len(rows)}")
    return rows[0]


def positional_key(params: dict[str, Any], index: int) -> str:
    if str(index) in params.get("positional_names", {}):
        return params["positional_names"][str(index)]
    if "field_names" in params:
        return params["field_names"][index - 1]
    return f"{params.get('positional_prefix', 'column_')}{index}"


def assign_field(fields: dict[str, str], key: str, value: str, policy: str) -> str | None:
    if key not in fields:
        fields[key] = value
        return None
    if policy == "error":
        return f"duplicate_field:{key}:используйте duplicate_policy=first|last при допустимых дублях"
    if policy == "last":
        fields[key] = value
    return None


def parse_csv(raw: str, params: dict[str, Any]) -> dict:
    allowed = {
        "delimiter", "quotechar", "escapechar", "doublequote", "skipinitialspace", "strict",
        "label_separator", "positional_prefix", "positional_names", "field_names",
        "strip_keys", "strip_values", "allow_positionals", "allow_empty_keys",
        "allow_empty_fields", "expected_columns", "duplicate_policy",
    }
    param_error = delimited_params(params, allowed)
    if param_error:
        return failure(raw, param_error)
    if not raw:
        return failure(raw, "empty_csv_record")
    delimiter = params.get("delimiter", ",")
    label_separator = params.get("label_separator")
    strip_keys = params.get("strip_keys", True)
    strip_values = params.get("strip_values", False)
    allow_positionals = params.get("allow_positionals", True)
    allow_empty_keys = params.get("allow_empty_keys", False)
    allow_empty_fields = params.get("allow_empty_fields", True)
    duplicate_policy = params.get("duplicate_policy", "error")
    try:
        row = read_delimited_row(raw, delimiter, params)
    except (csv.Error, ValueError) as error:
        return failure(raw, f"invalid_csv:{error}")
    if "expected_columns" in params and len(row) != params["expected_columns"]:
        return failure(raw, f"unexpected_columns:expected={params['expected_columns']}:actual={len(row)}")
    if "field_names" in params and len(row) != len(params["field_names"]):
        return failure(raw, f"field_names_mismatch:expected={len(params['field_names'])}:actual={len(row)}")
    fields = {}
    labeled = 0
    for index, cell in enumerate(row, 1):
        key = positional_key(params, index)
        value = cell
        if label_separator and label_separator in cell:
            key, value = cell.split(label_separator, 1)
            labeled += 1
        elif label_separator and not allow_positionals:
            return failure(raw, f"missing_csv_label:column={index}:value={cell!r}")
        key = key.strip() if strip_keys else key
        value = value.strip() if strip_values else value
        if not key and not allow_empty_keys:
            return failure(raw, f"empty_csv_key:column={index}:используйте allow_empty_keys=true при необходимости")
        if not value and not allow_empty_fields:
            return failure(raw, f"empty_csv_field:{key}:используйте allow_empty_fields=true при необходимости")
        duplicate_error = assign_field(fields, key, value, duplicate_policy)
        if duplicate_error:
            return failure(raw, duplicate_error)
    return {
        "ok": True,
        "fields": fields,
        "residual": "",
        "error": None,
        "evidence": {"columns": len(row), "labeled_fields": labeled, "positional_fields": len(row) - labeled},
    }


def parse_kv(raw: str, params: dict[str, Any]) -> dict:
    allowed = {
        "pair_separator", "key_value_separator", "quotechar", "escapechar", "doublequote",
        "skipinitialspace", "strict", "positional_prefix", "positional_names",
        "strip_keys", "strip_values", "allow_positionals", "allow_empty_keys",
        "allow_empty_values", "expected_pairs", "duplicate_policy",
    }
    param_error = delimited_params(params, allowed)
    if param_error:
        return failure(raw, param_error)
    if not raw:
        return failure(raw, "empty_kv_record")
    pair_separator = params.get("pair_separator", ",")
    key_value_separator = params.get("key_value_separator", "=")
    strip_keys = params.get("strip_keys", True)
    strip_values = params.get("strip_values", False)
    allow_positionals = params.get("allow_positionals", False)
    allow_empty_keys = params.get("allow_empty_keys", False)
    allow_empty_values = params.get("allow_empty_values", True)
    duplicate_policy = params.get("duplicate_policy", "error")
    try:
        row = read_delimited_row(raw, pair_separator, params)
    except (csv.Error, ValueError) as error:
        return failure(raw, f"invalid_kv:{error}")
    if "expected_pairs" in params and len(row) != params["expected_pairs"]:
        return failure(raw, f"unexpected_pairs:expected={params['expected_pairs']}:actual={len(row)}")
    fields = {}
    positional = 0
    for index, cell in enumerate(row, 1):
        if key_value_separator not in cell:
            if not allow_positionals:
                return failure(raw, f"missing_kv_separator:column={index}:value={cell!r}")
            key = positional_key(params, index)
            value = cell
            positional += 1
        else:
            key, value = cell.split(key_value_separator, 1)
        key = key.strip() if strip_keys else key
        value = value.strip() if strip_values else value
        if not key and not allow_empty_keys:
            return failure(raw, f"empty_kv_key:column={index}:используйте allow_empty_keys=true при необходимости")
        if not value and not allow_empty_values:
            return failure(raw, f"empty_kv_value:{key}:используйте allow_empty_values=true при необходимости")
        duplicate_error = assign_field(fields, key, value, duplicate_policy)
        if duplicate_error:
            return failure(raw, duplicate_error)
    return {
        "ok": True,
        "fields": fields,
        "residual": "",
        "error": None,
        "evidence": {"pairs": len(row), "positional_fields": positional},
    }


def try_parse(codec: str, raw: str, params: dict[str, Any] | None = None) -> dict:
    params = params or {}
    if codec == "grok":
        if set(params) != {"pattern", "patterns"}:
            return failure(raw, "invalid_grok_params: ожидаются pattern и patterns")
        if not isinstance(params["pattern"], str) or not isinstance(params["patterns"], dict):
            return failure(raw, "invalid_grok_params: pattern — строка, patterns — объект ИМЯ → regex")
        result = grok.execute_pattern(params["pattern"], raw, params["patterns"])
        return {
            "ok": result["ok"],
            "fields": result["captures"],
            "residual": result["residual"],
            "error": result["error"],
        }
    if codec == "cef":
        if params:
            return failure(raw, f"unsupported_params:{sorted(params)}:для cef параметры не требуются")
        return parse_cef(raw)
    if codec == "csv":
        return parse_csv(raw, params)
    if codec == "kv":
        return parse_kv(raw, params)
    return failure(raw, f"unsupported_codec:{codec}:expected=grok|cef|csv|kv")


def source_value(stages: list[PipelineStage], index: int, raw: str, context: dict) -> tuple[str | None, str | None]:
    stage = stages[index]
    if index == 0:
        if stage.source != "raw":
            return None, f"invalid_source:{stage.source}:первая стадия должна использовать raw"
        return raw, None
    prefix = f"stages.{stages[index - 1].name}.fields."
    if not stage.source.startswith(prefix):
        return None, f"invalid_source:{stage.source}:ожидается {prefix}<field>"
    field = stage.source[len(prefix):]
    if not field or "." in field:
        return None, f"invalid_source_field:{field}:укажите непосредственное поле предыдущей стадии"
    value = context.get(field)
    if not isinstance(value, str):
        return None, f"missing_source_field:{stage.source}:доступны {sorted(context)}"
    return value, None


def execute_pipeline(stages: list[PipelineStage], raw: str) -> dict:
    if not stages:
        return {
            "ok": False,
            "captures": {},
            "trace": [],
            "failed_layer": None,
            "residual": raw,
            "error": "invalid_arguments: stages должен содержать хотя бы одну стадию",
        }
    captures = {}
    context = {}
    trace = []
    current = raw
    stage_names = set()
    for index, stage in enumerate(stages):
        if stage.name in stage_names:
            return {
                "ok": False,
                "captures": captures,
                "trace": trace,
                "failed_layer": stage.name,
                "residual": current,
                "error": f"duplicate_stage_name:{stage.name}",
            }
        stage_names.add(stage.name)
        current, source_error = source_value(stages, index, raw, context)
        if source_error:
            trace.append({"stage": stage.name, "codec": stage.codec, "status": "error", "error": source_error})
            return {
                "ok": False,
                "captures": captures,
                "trace": trace,
                "failed_layer": stage.name,
                "residual": current or raw,
                "error": source_error,
            }
        result = try_parse(stage.codec, current, stage.params)
        trace.append({
            "stage": stage.name,
            "codec": stage.codec,
            "source": stage.source,
            "status": "ok" if result["ok"] else "error",
            "fields_extracted": len(result["fields"]),
            "error": result["error"],
        })
        if not result["ok"]:
            return {
                "ok": False,
                "captures": captures,
                "trace": trace,
                "failed_layer": stage.name,
                "residual": result["residual"],
                "error": result["error"],
            }
        collision = captures.keys() & result["fields"].keys()
        if collision:
            error = f"stage_field_collision:{stage.name}:{sorted(collision)}"
            return {
                "ok": False,
                "captures": captures,
                "trace": trace,
                "failed_layer": stage.name,
                "residual": current,
                "error": error,
            }
        captures.update(result["fields"])
        context = result["fields"]
        if result["residual"]:
            return {
                "ok": False,
                "captures": captures,
                "trace": trace,
                "failed_layer": stage.name,
                "residual": result["residual"],
                "error": "nonempty_stage_residual",
            }
    return {
        "ok": True,
        "captures": captures,
        "trace": trace,
        "failed_layer": None,
        "residual": "",
        "error": None,
    }


def evaluate_observations(
    stages: list[PipelineStage],
    record_schema: list[RecordField],
    observations: list[Observation],
    constraints: list[FieldConstraint] | None = None,
) -> dict:
    results = []
    for index, observation in enumerate(observations, 1):
        parser_result = ParserResult.model_validate(execute_pipeline(stages, observation.raw))
        validation = grok.validate_result(parser_result, record_schema, constraints)
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
    stages: list[PipelineStage],
    record_schema: list[RecordField],
    observations: list[Observation],
    baseline: ParserBaseline | None,
    constraints: list[FieldConstraint] | None = None,
) -> dict:
    candidate = evaluate_observations(stages, record_schema, observations, constraints)
    result = {"ok": candidate["ok"], "candidate": candidate}
    if baseline:
        if baseline.kind != "pipeline":
            previous = {"ok": False, "issues": ["baseline_kind_must_be_pipeline"]}
        else:
            previous = evaluate_observations(
                baseline.stages,
                baseline.record_schema or record_schema,
                baseline.observations,
                baseline.constraints,
            )
        result["baseline"] = previous
        result["ok"] = result["ok"] and previous["ok"]
    return result


def probe_format(raw: str) -> dict:
    candidates = []
    errors = {}
    cef = parse_cef(raw)
    if cef["ok"]:
        candidates.append({
            "format": "cef",
            "evidence": {"cef_header_and_extension_valid": True, "fields": len(cef["fields"])},
        })
    else:
        errors["cef_error"] = cef["error"]
    csv_params = {"delimiter": ",", "quotechar": '"', "label_separator": ":", "strip_values": True}
    csv_result = parse_csv(raw, csv_params)
    if csv_result["ok"] and len(csv_result["fields"]) > 1:
        candidates.append({
            "format": "csv",
            "params": csv_params,
            "evidence": csv_result["evidence"],
        })
    else:
        errors["csv_error"] = csv_result["error"]
    kv_params = {
        "pair_separator": ",",
        "key_value_separator": ":",
        "quotechar": '"',
        "allow_positionals": True,
        "strip_values": True,
    }
    kv_result = parse_kv(raw, kv_params)
    if kv_result["ok"] and len(kv_result["fields"]) > 1:
        candidates.append({
            "format": "kv",
            "params": kv_params,
            "evidence": kv_result["evidence"],
        })
    else:
        errors["kv_error"] = kv_result["error"]
    return {"ok": True, "candidates": candidates, "evidence": {} if candidates else errors}
