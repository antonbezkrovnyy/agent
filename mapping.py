from datetime import datetime, timezone
import ipaddress
from typing import Any

import normalization
import storage
import taxonomy
from schemas import MappingField, NormalizationField


def utc_datetime(value: Any, source_timezone: str | None) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        if not source_timezone:
            raise ValueError("naive_datetime_requires_source_timezone")
        parsed = parsed.replace(tzinfo=normalization.parse_timezone(source_timezone))
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def mapping_rank(target: str) -> int:
    if target in {
        "time", "id", "event_src.vendor", "event_src.title", "event_src.category",
        "action", "object", "status", "importance",
    }:
        return 0
    if target.startswith("category."):
        return 20
    if target == "subject" or target.startswith("subject."):
        return 30
    if target.startswith("object."):
        return 40
    if target.startswith(("src.", "dst.", "assigned_")):
        return 50
    if target.startswith("event_src."):
        return 60
    if target in {"protocol", "protocol.layer7", "direction", "duration", "start_time", "chain_id", "logon_auth_method", "logon_service", "logon_type", "reason"}:
        return 70
    return 80


def condition_matches(value: Any, condition: dict[str, Any] | None) -> bool:
    if condition is None:
        return True
    if set(condition) != {"format"}:
        raise ValueError(f"invalid_condition:{condition}:expected={{format: ip|ipv4|ipv6|hostname}}")
    format_name = condition["format"]
    if format_name in {"ip", "ipv4", "ipv6"}:
        try:
            address = ipaddress.ip_address(str(value))
        except ValueError:
            return False
        if format_name == "ip":
            return True
        return address.version == int(format_name[-1])
    if format_name == "hostname":
        return taxonomy.valid_hostname(value)
    raise ValueError(f"invalid_condition_format:{format_name}")


def apply_mappings(
    normalized: dict,
    mappings: list[MappingField],
    source_timezone: str | None,
) -> dict:
    event = {}
    errors = []
    targets = [mapping.target for mapping in mappings]
    duplicates = sorted({target for target in targets if targets.count(target) > 1})
    if duplicates:
        errors.append({"error": "duplicate_mapping_targets", "targets": duplicates})
    previous_rank = -1
    for mapping in mappings:
        rank = mapping_rank(mapping.target)
        if rank < previous_rank:
            errors.append({"error": "mapping_order_violation", "target": mapping.target, "rank": rank, "previous_rank": previous_rank})
        previous_rank = max(previous_rank, rank)
        if mapping.source is None:
            value = mapping.constant
        else:
            if mapping.source not in normalized:
                continue
            value = normalized[mapping.source]
            try:
                if not condition_matches(value, mapping.condition):
                    continue
            except (ValueError, TypeError) as error:
                errors.append({"source": mapping.source, "target": mapping.target, "error": str(error)})
                continue
            if mapping.value_map is not None:
                key = str(value)
                if key not in mapping.value_map:
                    errors.append({
                        "source": mapping.source,
                        "target": mapping.target,
                        "error": f"unmapped_source_value:{key}",
                    })
                    continue
                value = mapping.value_map[key]
        if mapping.target in {"time", "start_time"}:
            try:
                value = utc_datetime(value, source_timezone)
            except (ValueError, TypeError) as error:
                errors.append({"source": mapping.source, "target": mapping.target, "error": str(error)})
                continue
        event[mapping.target] = value
    validation = taxonomy.validate_event(event)
    errors.extend({"error": issue} for issue in validation["issues"])
    return {"ok": not errors, "event": event, "errors": errors}


def evaluate_mappings(
    family_id: str,
    parser_revision: int,
    normalization_revision: int,
    mappings: list[MappingField],
    evidence: list[str],
    source_timezone: str | None,
) -> dict:
    normalization_saved = storage.get_normalization(
        family_id,
        normalization_revision,
        parser_revision,
    )
    if not normalization_saved["ok"]:
        return normalization_saved
    normalization_data = normalization_saved["normalization"]
    family = storage.get_family(family_id, parser_revision)["family"]
    fields = [NormalizationField.model_validate(field) for field in normalization_data["fields"]]
    normalized_evaluation = normalization.evaluate_fields(family, fields, evidence)
    if not normalized_evaluation["ok"]:
        return {"ok": False, "error": "normalization_failed", "details": normalized_evaluation}
    results = []
    for index, normalized_result in enumerate(normalized_evaluation["results"], 1):
        mapped = apply_mappings(normalized_result["normalized"], mappings, source_timezone)
        results.append({
            "observation": index,
            "ok": mapped["ok"],
            "normalized": normalized_result["normalized"],
            **mapped,
        })
    return {"ok": bool(results) and all(result["ok"] for result in results), "results": results}


def preview_mapping(
    family_id: str,
    parser_revision: int,
    normalization_revision: int,
    mappings: list[MappingField],
    raw: str,
    source_timezone: str | None,
) -> dict:
    evaluation = evaluate_mappings(
        family_id,
        parser_revision,
        normalization_revision,
        mappings,
        [raw],
        source_timezone,
    )
    return {"ok": evaluation["ok"], "evaluation": evaluation}


def map_with_latest(family_id: str, parser_revision: int, raw: str) -> dict:
    normalized = normalization.normalize_with_latest(family_id, parser_revision, raw)
    if not normalized["ok"] or normalized["status"] == "not_configured":
        return normalized
    mapping_saved = storage.get_mapping(
        family_id,
        normalization_revision=normalized["normalization_revision"],
    )
    if not mapping_saved["ok"]:
        return {
            "ok": True,
            "parse_ok": True,
            "normalization_ok": True,
            "mapping_ok": None,
            "status": "not_mapped",
            "captures": normalized["captures"],
            "normalized": normalized["normalized"],
            "errors": [],
        }
    mapping_data = mapping_saved["mapping"]
    mappings = [MappingField.model_validate(item) for item in mapping_data["mappings"]]
    mapped = apply_mappings(
        normalized["normalized"],
        mappings,
        mapping_data["source_timezone"],
    )
    return {
        "ok": mapped["ok"],
        "parse_ok": True,
        "normalization_ok": True,
        "mapping_ok": mapped["ok"],
        "status": "mapped" if mapped["ok"] else "mapping_failed",
        "mapping_revision": mapping_data["revision"],
        "taxonomy": mapping_data["taxonomy"],
        "captures": normalized["captures"],
        "normalized": normalized["normalized"],
        **mapped,
    }
