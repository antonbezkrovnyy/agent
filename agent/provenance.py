from typing import Any


def source_metadata(state: dict) -> dict[str, Any]:
    record = next((
        item for item in reversed(state.get("evidence") or [])
        if item.get("tool") == "source_info" and item.get("status") == "ok"
    ), None)
    result = (record or {}).get("result") or {}
    return {
        "source": "earth-fixture" if result.get("fake_earth") else (result.get("source") or "aegra_checkpoint"),
        "tenant": result.get("tenant_ids") or state.get("tenant") or "unknown",
        "endpoint": result.get("endpoint") or state.get("source_endpoint") or "unknown",
        "schema_version": result.get("schema_version") or state.get("schema_version") or "unknown",
        "schema_fields": result.get("schema_fields"),
    }


def time_metadata(state: dict) -> tuple[str, dict]:
    axes = dict(state.get("time_axes") or {})
    for record in state.get("evidence") or []:
        if record.get("tool") != "field_lookup":
            continue
        for entry in (record.get("result") or {}).get("fields") or []:
            if not entry.get("ok"):
                continue
            name = str(entry.get("field") or "")
            type_name = str(entry.get("type") or "")
            if "time" not in name.lower() or not (name or type_name):
                continue
            axes[name] = {
                "type": type_name,
                "description": entry.get("description") or "",
                "validation": "field_lookup",
            }
    semantics = state.get("time_semantics") or (
        "fields_described_not_normalized" if axes else "not_validated"
    )
    return semantics, axes


def verification_summary(checks: list[dict], source: str = "") -> dict:
    if not checks:
        return {
            "status": "not_performed",
            "method": "",
            "source": "",
            "checked_at": "",
            "independent": False,
            "checks": [],
        }
    return {
        "status": "passed" if all(item.get("status") == "matched" for item in checks) else "partial",
        "method": "same_source_replay",
        "source": source or "same-source",
        "checked_at": max(str(item.get("checked_at") or "") for item in checks),
        "independent": False,
        "checks": checks,
    }
