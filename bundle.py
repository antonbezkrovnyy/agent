import argparse
import hashlib
import json
import os
from pathlib import Path
import re


SCHEMA_VERSION = 1
GROK_REFERENCE = re.compile(r"%\{([A-Za-z0-9_]+)(?::([A-Za-z_][A-Za-z0-9_]*))?\}")
REGEX_METACHARACTERS = set(".^$*+?()[]{}|")


def literal_definition(definition: str, patterns: dict[str, str], seen: set[str] | None = None) -> str | None:
    reference = GROK_REFERENCE.fullmatch(definition)
    if reference and reference.group(2) is None:
        name = reference.group(1)
        seen = set() if seen is None else seen
        if name in seen or name not in patterns:
            return None
        return literal_definition(patterns[name], patterns, seen | {name})
    result = []
    index = 0
    while index < len(definition):
        char = definition[index]
        if char in REGEX_METACHARACTERS:
            return None
        if char == "\\":
            if index + 1 == len(definition) or definition[index + 1].isalnum():
                return None
            index += 1
            char = definition[index]
        result.append(char)
        index += 1
    return "".join(result) or None


def static_literal_runs(pattern: str) -> list[str]:
    runs = []
    current = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("%{", index):
            end = pattern.find("}", index + 2)
            if end < 0:
                break
            if current:
                runs.append("".join(current).strip())
                current = []
            index = end + 1
            continue
        char = pattern[index]
        if char in REGEX_METACHARACTERS:
            if current:
                runs.append("".join(current).strip())
                current = []
            index += 1
            continue
        if char == "\\":
            if index + 1 == len(pattern) or pattern[index + 1].isalnum():
                if current:
                    runs.append("".join(current).strip())
                    current = []
                index += 1
                continue
            index += 1
            char = pattern[index]
        current.append(char)
        index += 1
    if current:
        runs.append("".join(current).strip())
    return [run for run in runs if len(run) >= 4 and any(char.isalnum() for char in run)]


def build_dispatch(family: dict) -> dict:
    if not family["layers"]:
        return {"strategy": "always_candidates", "anchors": [], "fallback": True}
    definitions = {pattern["name"]: pattern["definition"] for pattern in family["patterns"]}
    candidates = static_literal_runs(family["layers"][0]["pattern"])
    for reference in GROK_REFERENCE.finditer(family["layers"][0]["pattern"]):
        if reference.group(2) and reference.group(1) in definitions:
            literal = literal_definition(definitions[reference.group(1)], definitions)
            if literal:
                candidates.append(literal)
    evidence = family["evidence"]
    static_fields = {
        field["name"] for field in family["record_schema"]
        if field["category"] == "static" and field["required"]
    }
    if static_fields and evidence:
        import runtime

        static_values = {name: set() for name in static_fields}
        for raw in evidence:
            parsed = runtime.execute_family(family, raw)
            if parsed["ok"]:
                for name in static_fields:
                    if name in parsed["captures"]:
                        static_values[name].add(parsed["captures"][name])
        candidates.extend(values.pop() for values in static_values.values() if len(values) == 1)
    anchors = sorted({
        candidate for candidate in candidates
        if evidence and all(candidate in raw for raw in evidence)
    })
    return {
        "strategy": "aho_corasick_all_anchors" if anchors else "always_candidates",
        "anchors": anchors,
        "fallback": not anchors,
    }


def compatibility_hash(bundle: dict) -> str:
    payload = {key: value for key, value in bundle.items() if key != "compatibility_hash"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def export_bundle(args: argparse.Namespace) -> dict:
    os.environ["RESEARCH_DB"] = args.database
    import storage

    saved_mapping = storage.get_mapping(args.family_id, args.mapping_revision)
    if not saved_mapping["ok"]:
        raise ValueError(saved_mapping["message"])
    mapping = saved_mapping["mapping"]
    parser_revision = args.parser_revision or mapping["parser_revision"]
    normalization_revision = args.normalization_revision or mapping["normalization_revision"]
    if parser_revision != mapping["parser_revision"]:
        raise ValueError("mapping revision несовместима с запрошенной parser revision")
    if normalization_revision != mapping["normalization_revision"]:
        raise ValueError("mapping revision несовместима с запрошенной normalization revision")

    saved_family = storage.get_family(args.family_id, parser_revision)
    if not saved_family["ok"]:
        raise ValueError(saved_family["message"])
    saved_normalization = storage.get_normalization(
        args.family_id,
        normalization_revision,
        parser_revision,
    )
    if not saved_normalization["ok"]:
        raise ValueError(saved_normalization["message"])

    taxonomy_name = mapping["taxonomy"]
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "family_id": args.family_id,
        "dispatch": build_dispatch(saved_family["family"]),
        "parser": saved_family["family"],
        "normalization": saved_normalization["normalization"],
        "mapping": mapping,
        "taxonomy": {
            "name": taxonomy_name,
            "fields": storage.list_taxonomy_fields(taxonomy_name),
            "values": storage.get_taxonomy_values(taxonomy_name)["values"],
            "categories": storage.get_taxonomy_categories(taxonomy_name)["categories"],
        },
    }
    bundle["compatibility_hash"] = compatibility_hash(bundle)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "output": args.output,
        "family_id": args.family_id,
        "parser_revision": parser_revision,
        "normalization_revision": normalization_revision,
        "mapping_revision": mapping["revision"],
        "compatibility_hash": bundle["compatibility_hash"],
        "evidence": len(mapping["evidence"]),
    }


def python_result(bundle: dict, raw: str) -> dict:
    import grok
    import mapping
    import normalization
    import runtime
    from schemas import FieldConstraint, NormalizationField, ParserResult, RecordField

    family = bundle["parser"]
    parsed = runtime.execute_family(family, raw)
    validation = grok.validate_result(
        ParserResult.model_validate(parsed),
        [RecordField.model_validate(field) for field in family["record_schema"]],
        [FieldConstraint.model_validate(rule) for rule in family["constraints"]],
    )
    if not validation["ok"]:
        return {
            "ok": False,
            "status": "parse_failed",
            "captures": parsed["captures"],
            "errors": validation["issues"],
        }
    fields = [
        NormalizationField.model_validate(field)
        for field in bundle["normalization"]["fields"]
    ]
    normalized = normalization.normalize_captures(parsed["captures"], fields)
    if not normalized["ok"]:
        return {
            "ok": False,
            "status": "normalization_failed",
            "captures": parsed["captures"],
            "normalized": normalized["normalized"],
            "errors": normalized["errors"],
        }
    from schemas import MappingField

    mapped = mapping.apply_mappings(
        normalized["normalized"],
        [MappingField.model_validate(item) for item in bundle["mapping"]["mappings"]],
        bundle["mapping"]["source_timezone"],
    )
    return {
        "ok": mapped["ok"],
        "status": "mapped" if mapped["ok"] else "mapping_failed",
        "captures": parsed["captures"],
        "normalized": normalized["normalized"],
        "event": mapped["event"],
        "errors": mapped["errors"],
    }


def export_golden(args: argparse.Namespace) -> dict:
    os.environ["RESEARCH_DB"] = args.database
    bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    if compatibility_hash(bundle) != bundle.get("compatibility_hash"):
        raise ValueError("compatibility_hash не совпадает с содержимым bundle")
    cases = [
        {"raw": raw, "expected": python_result(bundle, raw)}
        for raw in bundle["mapping"]["evidence"]
    ]
    golden = {
        "schema_version": SCHEMA_VERSION,
        "family_id": bundle["family_id"],
        "compatibility_hash": bundle["compatibility_hash"],
        "cases": cases,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(golden, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "output": args.output,
        "cases": len(cases),
        "passed_python": sum(case["expected"]["ok"] for case in cases),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Экспорт versioned Family Bundle и Python golden results.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="Экспортировать исполняемый Family Bundle из Registry.")
    export.add_argument("--database", required=True)
    export.add_argument("--family-id", required=True)
    export.add_argument("--parser-revision", type=int)
    export.add_argument("--normalization-revision", type=int)
    export.add_argument("--mapping-revision", type=int)
    export.add_argument("--output", required=True)

    golden = subparsers.add_parser("golden", help="Сохранить эталонные Python results для evidence bundle.")
    golden.add_argument("--database", required=True)
    golden.add_argument("--bundle", required=True)
    golden.add_argument("--output", required=True)

    args = parser.parse_args()
    result = export_bundle(args) if args.command == "export" else export_golden(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
