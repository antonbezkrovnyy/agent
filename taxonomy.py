from datetime import datetime
import ipaddress
import json
from pathlib import Path
import re

import semantic
import storage


TAXONOMY = "mp_siem_8.0"
REFERENCE = Path("reference/mpsiem")


def parse_reference() -> dict:
    text = (REFERENCE / "mp_siem_8_taxonomy_reference.md").read_text(encoding="utf-8")
    fields = []
    values = []
    current_enum = None
    for line in text.splitlines():
        heading = re.match(r"### 5\.\d+\. `([^`]+)`", line)
        if heading:
            current_enum = heading.group(1)
            continue
        range_row = re.match(
            r"^\| `(datafield|numfield)1` … `(?:datafield|numfield)(\d+)` \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$",
            line,
        )
        if range_row:
            prefix, maximum, required, field_type, description = (item.strip() for item in range_row.groups())
            for index in range(1, int(maximum) + 1):
                fields.append({
                    "name": f"{prefix}{index}",
                    "type": field_type.strip("`"),
                    "required": required == "Да" or required.startswith("Нормализация"),
                    "automatic": required == "Авто",
                    "description": description,
                })
            continue
        row = re.match(r"^\| `([^`]+)` \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$", line)
        if row:
            name, required, field_type, description = (item.strip() for item in row.groups())
            names = [name]
            if name == "datafield1":
                names = [f"datafield{index}" for index in range(1, 11)]
            elif name == "numfield1":
                names = [f"numfield{index}" for index in range(1, 6)]
            for expanded in names:
                fields.append({
                    "name": expanded,
                    "type": field_type.strip("`"),
                    "required": required == "Да" or required.startswith("Нормализация"),
                    "automatic": required == "Авто",
                    "description": description,
                })
            continue
        if current_enum:
            enum_row = re.match(r"^\| `([^`]+)` \| ([^|]+) \|$", line)
            if enum_row:
                values.append({
                    "field": current_enum,
                    "value": enum_row.group(1),
                    "description": enum_row.group(2).strip(),
                })
    category_section = text.split("## 6. Значения `event_src.category`", 1)[1]
    category_block = re.search(r"```text\n(.*?)\n```", category_section, re.DOTALL)
    if not category_block:
        raise ValueError("event_src.category reference block not found")
    for value in category_block.group(1).splitlines():
        value = value.strip()
        if value:
            values.append({
                "field": "event_src.category",
                "value": value,
                "description": f"Категория приложения или узла, зарегистрировавшего событие: {value}.",
            })
    categories = []
    generic = ""
    high = ""
    for line in (REFERENCE / "mp_siem_8_event_categories.md").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(\s*)- `([^`]+)`", line)
        if not match:
            continue
        level = len(match.group(1)) // 2
        value = match.group(2)
        if level == 0:
            generic = value
            high = ""
            continue
        if level == 1:
            high = value
            continue
        if level != 2:
            continue
        categories.append({
            "generic": generic,
            "high": high,
            "low": value,
            "description": f"Категория события MP SIEM: {generic} → {high} → {value}.",
        })
    return {"fields": fields, "values": values, "categories": categories}


def taxonomy_document(kind: str, item: dict) -> str:
    if kind == "taxonomy_field":
        return (
            f"MP SIEM field: {item['name']}\nType: {item['type']}\n"
            f"Required: {item['required']}\nAutomatic: {item['automatic']}\nDescription: {item['description']}"
        )
    if kind == "taxonomy_value":
        return f"MP SIEM enum field: {item['field']}\nValue: {item['value']}\nDescription: {item['description']}"
    return f"MP SIEM event category: {item['generic']} → {item['high']} → {item['low']}\n{item['description']}"


def entity_id(kind: str, item: dict) -> str:
    if kind == "taxonomy_field":
        return f"{TAXONOMY}:{item['name']}"
    if kind == "taxonomy_value":
        return f"{TAXONOMY}:{item['field']}:{item['value']}"
    return f"{TAXONOMY}:{item['generic']}:{item['high']}:{item['low']}"


def index_taxonomy() -> dict:
    fields = storage.list_taxonomy_fields(TAXONOMY)
    for item in fields:
        semantic.ensure_embedding("taxonomy_field", entity_id("taxonomy_field", item), taxonomy_document("taxonomy_field", item))
    values = storage.get_taxonomy_values(TAXONOMY)["values"]
    for item in values:
        semantic.ensure_embedding("taxonomy_value", entity_id("taxonomy_value", item), taxonomy_document("taxonomy_value", item))
    categories = storage.get_taxonomy_categories(TAXONOMY)["categories"]
    for item in categories:
        semantic.ensure_embedding("taxonomy_category", entity_id("taxonomy_category", item), taxonomy_document("taxonomy_category", item))
    return {"fields": len(fields), "values": len(values), "categories": len(categories)}


def load_taxonomy() -> dict:
    parsed = parse_reference()
    saved = storage.replace_taxonomy(TAXONOMY, parsed["fields"], parsed["values"], parsed["categories"])
    saved["embeddings"] = index_taxonomy()
    return saved


def search_taxonomy(kind: str, query: str, field: str | None = None) -> dict:
    if kind not in {"taxonomy_field", "taxonomy_value", "taxonomy_category"}:
        return {"ok": False, "error": "invalid_taxonomy_kind", "message": "Ожидается taxonomy_field, taxonomy_value или taxonomy_category."}
    if kind == "taxonomy_field":
        items = storage.list_taxonomy_fields(TAXONOMY)
    elif kind == "taxonomy_value":
        items = storage.get_taxonomy_values(TAXONOMY, field)["values"]
    else:
        items = storage.get_taxonomy_categories(TAXONOMY)["categories"]
    for item in items:
        semantic.ensure_embedding(kind, entity_id(kind, item), taxonomy_document(kind, item))
    scores = {item["entity_id"]: item["score"] for item in semantic.search_embeddings(kind, query)}
    results = [
        {**item, "score": scores.get(entity_id(kind, item))}
        for item in items
    ]
    results.sort(key=lambda item: item["score"] if item["score"] is not None else -1, reverse=True)
    return {"ok": True, "kind": kind, "query": query, "results": results}


def mapping_guidelines() -> dict:
    return {
        "ok": True,
        "taxonomy": TAXONOMY,
        "sequence": (REFERENCE / "mp_siem_8_mapping_sequence.md").read_text(encoding="utf-8"),
    }


def valid_hostname(value) -> bool:
    text = str(value)
    if not text or any(char.isspace() for char in text) or "/" in text:
        return False
    try:
        ipaddress.ip_address(text)
        return False
    except ValueError:
        pass
    return re.fullmatch(r"[A-Za-z0-9_.\-]+", text) is not None


def datetime_utc(value) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


def validate_event(event: dict) -> dict:
    fields = {item["name"]: item for item in storage.list_taxonomy_fields(TAXONOMY)}
    issues = []
    for name, definition in fields.items():
        if definition["required"] and (name not in event or event[name] is None or event[name] == ""):
            issues.append(f"missing_required_field:{name}")
    values = storage.get_taxonomy_values(TAXONOMY)["values"]
    enum_values = {}
    for item in values:
        enum_values.setdefault(item["field"], set()).add(item["value"])
    for name, value in event.items():
        definition = fields.get(name)
        if not definition:
            issues.append(f"unknown_taxonomy_field:{name}")
            continue
        if definition["automatic"]:
            issues.append(f"automatic_field_forbidden:{name}")
        field_type = definition["type"].strip("`")
        if field_type == "Number" and (not isinstance(value, int) or isinstance(value, bool)):
            issues.append(f"invalid_number:{name}:{value!r}")
        elif field_type == "IPAddress":
            try:
                ipaddress.ip_address(str(value))
            except ValueError:
                issues.append(f"invalid_ip:{name}:{value!r}")
        elif field_type == "DateTime" and not datetime_utc(value):
            issues.append(f"invalid_datetime_utc:{name}:{value!r}")
        elif field_type == "Enum" and value not in enum_values.get(name, set()):
            issues.append(f"invalid_enum:{name}:{value!r}:allowed={sorted(enum_values.get(name, []))}")
        elif field_type == "String" and not isinstance(value, str):
            issues.append(f"invalid_string:{name}:{value!r}")
    for name in ("event_src.vendor", "event_src.title"):
        value = event.get(name)
        if isinstance(value, str) and (value != value.lower() or any(char.isspace() for char in value)):
            issues.append(f"invalid_source_name:{name}:{value!r}")
    if "id" in event and (
        not isinstance(event["id"], str)
        or not event["id"]
        or any(char.isspace() for char in event["id"])
        or len(event["id"].split("_")) < 5
    ):
        issues.append("invalid_id_format:expected=namespace_vendor_title_transport_event-type")
    if "event_src.hostname" in event and not valid_hostname(event["event_src.hostname"]):
        issues.append(f"invalid_event_src_hostname:{event['event_src.hostname']!r}")
    category = {name: event.get(name, "") for name in ("category.generic", "category.high", "category.low")}
    if any(category.values()):
        categories = storage.get_taxonomy_categories(TAXONOMY)["categories"]
        if not any(
            all(not category[key] or item[key.split(".")[1]] == category[key] for key in category)
            for item in categories
        ):
            issues.append(f"invalid_category_hierarchy:{json.dumps(category, ensure_ascii=False)}")
    return {"ok": not issues, "issues": issues}
