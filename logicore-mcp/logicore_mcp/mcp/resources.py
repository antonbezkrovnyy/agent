"""Resources: пассивный контекст модели (TD-0001)."""

import json


def _error_payload(error: str, hint: str | None = None) -> str:
    return json.dumps({"ok": False, "error": error, "hint": hint}, ensure_ascii=False)


def render_fields_resource(schema: dict) -> str:
    """Компактный словарь полей группами по префиксам, без описаний (TD-0001, R-0004)."""
    groups: dict[str, list[str]] = {}
    for name in sorted(schema):
        prefix = name.split("_", 1)[0] if "_" in name else name[:4]
        groups.setdefault(prefix, []).append(name)
    return json.dumps(
        {"ok": True, "fields_total": len(schema), "groups": groups},
        ensure_ascii=False,
    )
