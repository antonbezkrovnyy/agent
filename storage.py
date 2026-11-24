from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timezone


def grok_dependencies(definition: str) -> list[str]:
    return sorted(set(re.findall(r"%\{([A-Za-z0-9_]+)(?::[A-Za-z_][A-Za-z0-9_]*)?\}", definition)))


@contextmanager
def db_connect() -> Iterator[sqlite3.Connection]:
    path = Path(os.getenv("RESEARCH_DB", "research/log_research.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS grok_patterns (
            name TEXT PRIMARY KEY,
            definition TEXT NOT NULL,
            description TEXT NOT NULL,
            positive_examples_json TEXT NOT NULL,
            negative_examples_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS parser_families (
            family_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            patterns_json TEXT NOT NULL,
            layers_json TEXT NOT NULL,
            stages_json TEXT NOT NULL DEFAULT '[]',
            record_schema_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            family_description_json TEXT NOT NULL,
            parser_description_json TEXT NOT NULL,
            constraints_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (family_id, revision)
        );
        CREATE TABLE IF NOT EXISTS taxonomy_fields (
            taxonomy TEXT NOT NULL,
            name TEXT NOT NULL,
            field_type TEXT NOT NULL,
            required INTEGER NOT NULL,
            automatic INTEGER NOT NULL,
            description TEXT NOT NULL,
            PRIMARY KEY (taxonomy, name)
        );
        CREATE TABLE IF NOT EXISTS taxonomy_values (
            taxonomy TEXT NOT NULL,
            field TEXT NOT NULL,
            value TEXT NOT NULL,
            description TEXT NOT NULL,
            PRIMARY KEY (taxonomy, field, value)
        );
        CREATE TABLE IF NOT EXISTS taxonomy_categories (
            taxonomy TEXT NOT NULL,
            generic TEXT NOT NULL,
            high TEXT NOT NULL,
            low TEXT NOT NULL,
            description TEXT NOT NULL,
            PRIMARY KEY (taxonomy, generic, high, low)
        );
        CREATE TABLE IF NOT EXISTS mapping_revisions (
            family_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            parser_revision INTEGER NOT NULL,
            normalization_revision INTEGER NOT NULL,
            taxonomy TEXT NOT NULL,
            source_timezone TEXT,
            mappings_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (family_id, revision)
        );
        CREATE TABLE IF NOT EXISTS normalization_revisions (
            family_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            parser_revision INTEGER NOT NULL,
            fields_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (family_id, revision)
        );
        CREATE TABLE IF NOT EXISTS research_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS semantic_embeddings_v2 (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            index_version TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            model_name TEXT NOT NULL,
            dimension INTEGER NOT NULL,
            vector_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (entity_type, entity_id, index_version)
        );
    """)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(parser_families)").fetchall()
    }
    for column in ("family_description_json", "parser_description_json", "constraints_json", "stages_json"):
        if column not in columns:
            default = "[]" if column in ("constraints_json", "stages_json") else "{}"
            connection.execute(
                f"ALTER TABLE parser_families ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'"
            )
    grok_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(grok_patterns)").fetchall()
    }
    for column in ("positive_examples_json", "negative_examples_json"):
        if column not in grok_columns:
            connection.execute(
                f"ALTER TABLE grok_patterns ADD COLUMN {column} TEXT NOT NULL DEFAULT '[]'"
            )
    connection.execute("UPDATE parser_families SET constraints_json = '[]' WHERE constraints_json = '{}'")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def save_grok(
    name: str,
    definition: str,
    description: str,
    positive_examples: list[str],
    negative_examples: list[str],
) -> dict:
    with db_connect() as connection:
        existing = connection.execute(
            "SELECT definition, description, positive_examples_json, negative_examples_json "
            "FROM grok_patterns WHERE name = ?",
            (name,),
        ).fetchone()
        payload = (
            definition,
            description,
            json.dumps(positive_examples, ensure_ascii=False),
            json.dumps(negative_examples, ensure_ascii=False),
        )
        if existing and existing != payload:
            return {
                "ok": False,
                "error": "pattern_exists_with_different_definition",
                "message": "Выберите новое имя или получите существующее определение через get_grok_pattern.",
                "name": name,
            }
        if existing:
            return {"ok": True, "name": name, "status": "unchanged"}
        connection.execute(
            "INSERT INTO grok_patterns ("
            "name, definition, description, positive_examples_json, negative_examples_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (name, *payload, datetime.now(timezone.utc).isoformat()),
        )
    return {"ok": True, "name": name, "status": "saved"}


def get_grok(name: str) -> dict:
    with db_connect() as connection:
        row = connection.execute(
            "SELECT name, definition, description, positive_examples_json, negative_examples_json, created_at "
            "FROM grok_patterns WHERE name = ?",
            (name,),
        ).fetchone()
    if not row:
        return {
            "ok": False,
            "error": "not_found",
            "message": "Проверьте точное имя или найдите конструкцию через search_grok_patterns.",
            "name": name,
        }
    return {
        "ok": True,
        "pattern": {
            "name": row[0],
            "definition": row[1],
            "description": row[2],
            "positive_examples": json.loads(row[3]),
            "negative_examples": json.loads(row[4]),
            "dependencies": grok_dependencies(row[1]),
            "created_at": row[5],
        },
    }


def search_grok(query: str) -> dict:
    like = f"%{query}%"
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT name, definition, description, positive_examples_json, negative_examples_json "
            "FROM grok_patterns WHERE name LIKE ? OR definition LIKE ? OR description LIKE ? LIMIT 20",
            (like, like, like),
        ).fetchall()
    return {
        "ok": True,
        "patterns": [
            {
                "name": row[0],
                "definition": row[1],
                "description": row[2],
                "positive_examples": json.loads(row[3]),
                "negative_examples": json.loads(row[4]),
                "dependencies": grok_dependencies(row[1]),
            }
            for row in rows
        ],
    }


def save_family(
    family_id: str,
    revision: int,
    patterns: list[dict],
    layers: list[dict],
    record_schema: list[dict],
    evidence: list[str],
    family_description: dict,
    parser_description: dict,
    constraints: list[dict],
    stages: list[dict] | None = None,
) -> dict:
    with db_connect() as connection:
        existing = connection.execute(
            "SELECT patterns_json, layers_json, record_schema_json, evidence_json, "
            "family_description_json, parser_description_json, constraints_json, stages_json "
            "FROM parser_families WHERE family_id = ? AND revision = ?",
            (family_id, revision),
        ).fetchone()
        payload = (
            json.dumps(patterns, ensure_ascii=False),
            json.dumps(layers, ensure_ascii=False),
            json.dumps(record_schema, ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False),
            json.dumps(family_description, ensure_ascii=False),
            json.dumps(parser_description, ensure_ascii=False),
            json.dumps(constraints, ensure_ascii=False),
            json.dumps(stages or [], ensure_ascii=False),
        )
        if existing and existing != payload:
            return {
                "ok": False,
                "error": "revision_exists_with_different_content",
                "message": "Получите существующую ревизию через get_parser_family или увеличьте revision.",
                "family_id": family_id,
                "revision": revision,
            }
        if existing:
            return {"ok": True, "family_id": family_id, "revision": revision, "status": "unchanged"}
        connection.execute(
            "INSERT INTO parser_families ("
            "family_id, revision, patterns_json, layers_json, record_schema_json, evidence_json, "
            "family_description_json, parser_description_json, constraints_json, stages_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (family_id, revision, *payload, datetime.now(timezone.utc).isoformat()),
        )
    return {"ok": True, "family_id": family_id, "revision": revision, "status": "saved"}


def list_families() -> dict:
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT family_id, revision, evidence_json, family_description_json "
            "FROM parser_families AS current "
            "WHERE revision = ("
            "SELECT MAX(revision) FROM parser_families "
            "WHERE family_id = current.family_id"
            ") ORDER BY family_id"
        ).fetchall()
    return {
        "ok": True,
        "families": [
            {
                "family_id": row[0],
                "revision": row[1],
                "evidence": json.loads(row[2]),
                "description": json.loads(row[3]),
            }
            for row in rows
        ],
    }


def list_parser_revisions() -> list[dict]:
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT family_id, revision FROM parser_families "
            "ORDER BY family_id, revision DESC"
        ).fetchall()
    return [{"family_id": row[0], "revision": row[1]} for row in rows]


def get_family(family_id: str, revision: int | None = None) -> dict:
    with db_connect() as connection:
        if revision is None:
            row = connection.execute(
                "SELECT revision, patterns_json, layers_json, record_schema_json, evidence_json, "
                "family_description_json, parser_description_json, constraints_json, stages_json "
                "FROM parser_families WHERE family_id = ? ORDER BY revision DESC LIMIT 1",
                (family_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT revision, patterns_json, layers_json, record_schema_json, evidence_json, "
                "family_description_json, parser_description_json, constraints_json, stages_json "
                "FROM parser_families WHERE family_id = ? AND revision = ?",
                (family_id, revision),
            ).fetchone()
    if not row:
        return {
            "ok": False,
            "error": "not_found",
            "message": "Проверьте family_id и revision; для новой грамматики создайте новую Family.",
            "family_id": family_id,
            "revision": revision,
        }
    return {
        "ok": True,
        "family": {
            "family_id": family_id,
            "revision": row[0],
            "patterns": json.loads(row[1]),
            "layers": json.loads(row[2]),
            "record_schema": json.loads(row[3]),
            "evidence": json.loads(row[4]),
            "family_description": json.loads(row[5]),
            "parser_description": json.loads(row[6]),
            "constraints": json.loads(row[7]),
            "stages": json.loads(row[8]),
        },
    }


def save_normalization(
    family_id: str,
    revision: int,
    parser_revision: int,
    fields: list[dict],
    evidence: list[str],
    description: str,
) -> dict:
    with db_connect() as connection:
        existing = connection.execute(
            "SELECT parser_revision, fields_json, evidence_json, description "
            "FROM normalization_revisions WHERE family_id = ? AND revision = ?",
            (family_id, revision),
        ).fetchone()
        payload = (
            parser_revision,
            json.dumps(fields, ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False),
            description,
        )
        if existing and existing != payload:
            return {
                "ok": False,
                "error": "normalization_revision_exists_with_different_content",
                "message": "Получите существующую normalization revision или увеличьте revision.",
                "family_id": family_id,
                "revision": revision,
            }
        if existing:
            return {"ok": True, "family_id": family_id, "revision": revision, "status": "unchanged"}
        connection.execute(
            "INSERT INTO normalization_revisions ("
            "family_id, revision, parser_revision, fields_json, evidence_json, description, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (family_id, revision, *payload, datetime.now(timezone.utc).isoformat()),
        )
    return {"ok": True, "family_id": family_id, "revision": revision, "status": "saved"}


def get_normalization(
    family_id: str,
    revision: int | None = None,
    parser_revision: int | None = None,
) -> dict:
    clauses = ["family_id = ?"]
    params = [family_id]
    if revision is not None:
        clauses.append("revision = ?")
        params.append(revision)
    if parser_revision is not None:
        clauses.append("parser_revision = ?")
        params.append(parser_revision)
    with db_connect() as connection:
        row = connection.execute(
            "SELECT revision, parser_revision, fields_json, evidence_json, description, created_at "
            f"FROM normalization_revisions WHERE {' AND '.join(clauses)} "
            "ORDER BY revision DESC LIMIT 1",
            params,
        ).fetchone()
    if not row:
        return {
            "ok": False,
            "error": "not_found",
            "message": "Normalization revision не найдена; проверьте family_id и revisions.",
            "family_id": family_id,
            "revision": revision,
            "parser_revision": parser_revision,
        }
    return {
        "ok": True,
        "normalization": {
            "family_id": family_id,
            "revision": row[0],
            "parser_revision": row[1],
            "fields": json.loads(row[2]),
            "evidence": json.loads(row[3]),
            "description": row[4],
            "created_at": row[5],
        },
    }


def replace_taxonomy(taxonomy: str, fields: list[dict], values: list[dict], categories: list[dict]) -> dict:
    with db_connect() as connection:
        connection.execute("DELETE FROM taxonomy_fields WHERE taxonomy = ?", (taxonomy,))
        connection.execute("DELETE FROM taxonomy_values WHERE taxonomy = ?", (taxonomy,))
        connection.execute("DELETE FROM taxonomy_categories WHERE taxonomy = ?", (taxonomy,))
        connection.executemany(
            "INSERT INTO taxonomy_fields (taxonomy, name, field_type, required, automatic, description) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(taxonomy, item["name"], item["type"], int(item["required"]), int(item["automatic"]), item["description"]) for item in fields],
        )
        connection.executemany(
            "INSERT INTO taxonomy_values (taxonomy, field, value, description) VALUES (?, ?, ?, ?)",
            [(taxonomy, item["field"], item["value"], item["description"]) for item in values],
        )
        connection.executemany(
            "INSERT INTO taxonomy_categories (taxonomy, generic, high, low, description) VALUES (?, ?, ?, ?, ?)",
            [(taxonomy, item["generic"], item["high"], item["low"], item["description"]) for item in categories],
        )
    return {"ok": True, "taxonomy": taxonomy, "fields": len(fields), "values": len(values), "categories": len(categories)}


def list_taxonomy_fields(taxonomy: str) -> list[dict]:
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT name, field_type, required, automatic, description FROM taxonomy_fields WHERE taxonomy = ? ORDER BY name",
            (taxonomy,),
        ).fetchall()
    return [
        {"name": row[0], "type": row[1], "required": bool(row[2]), "automatic": bool(row[3]), "description": row[4]}
        for row in rows
    ]


def get_taxonomy_field(taxonomy: str, name: str) -> dict:
    with db_connect() as connection:
        row = connection.execute(
            "SELECT name, field_type, required, automatic, description FROM taxonomy_fields WHERE taxonomy = ? AND name = ?",
            (taxonomy, name),
        ).fetchone()
    if not row:
        return {"ok": False, "error": "not_found", "message": "Проверьте точное имя поля таксономии.", "taxonomy": taxonomy, "field": name}
    return {"ok": True, "field": {"name": row[0], "type": row[1], "required": bool(row[2]), "automatic": bool(row[3]), "description": row[4]}}


def get_taxonomy_values(taxonomy: str, field: str | None = None) -> dict:
    with db_connect() as connection:
        if field:
            rows = connection.execute(
                "SELECT field, value, description FROM taxonomy_values WHERE taxonomy = ? AND field = ? ORDER BY value",
                (taxonomy, field),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT field, value, description FROM taxonomy_values WHERE taxonomy = ? ORDER BY field, value",
                (taxonomy,),
            ).fetchall()
    return {"ok": True, "values": [{"field": row[0], "value": row[1], "description": row[2]} for row in rows]}


def get_taxonomy_categories(taxonomy: str) -> dict:
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT generic, high, low, description FROM taxonomy_categories WHERE taxonomy = ? ORDER BY generic, high, low",
            (taxonomy,),
        ).fetchall()
    return {"ok": True, "categories": [{"generic": row[0], "high": row[1], "low": row[2], "description": row[3]} for row in rows]}


def save_mapping(
    family_id: str,
    revision: int,
    parser_revision: int,
    normalization_revision: int,
    taxonomy: str,
    source_timezone: str | None,
    mappings: list[dict],
    evidence: list[str],
    description: str,
) -> dict:
    with db_connect() as connection:
        existing = connection.execute(
            "SELECT parser_revision, normalization_revision, taxonomy, source_timezone, mappings_json, evidence_json, description "
            "FROM mapping_revisions WHERE family_id = ? AND revision = ?",
            (family_id, revision),
        ).fetchone()
        payload = (
            parser_revision,
            normalization_revision,
            taxonomy,
            source_timezone,
            json.dumps(mappings, ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False),
            description,
        )
        if existing and existing != payload:
            return {"ok": False, "error": "mapping_revision_exists_with_different_content", "message": "Получите существующую mapping revision или увеличьте revision."}
        if existing:
            return {"ok": True, "family_id": family_id, "revision": revision, "status": "unchanged"}
        connection.execute(
            "INSERT INTO mapping_revisions (family_id, revision, parser_revision, normalization_revision, taxonomy, "
            "source_timezone, mappings_json, evidence_json, description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (family_id, revision, *payload, datetime.now(timezone.utc).isoformat()),
        )
    return {"ok": True, "family_id": family_id, "revision": revision, "status": "saved"}


def get_mapping(
    family_id: str,
    revision: int | None = None,
    normalization_revision: int | None = None,
) -> dict:
    clauses = ["family_id = ?"]
    params = [family_id]
    if revision is not None:
        clauses.append("revision = ?")
        params.append(revision)
    if normalization_revision is not None:
        clauses.append("normalization_revision = ?")
        params.append(normalization_revision)
    with db_connect() as connection:
        row = connection.execute(
            "SELECT revision, parser_revision, normalization_revision, taxonomy, source_timezone, mappings_json, "
            f"evidence_json, description, created_at FROM mapping_revisions WHERE {' AND '.join(clauses)} "
            "ORDER BY revision DESC LIMIT 1",
            params,
        ).fetchone()
    if not row:
        return {"ok": False, "error": "not_found", "message": "Mapping revision не найдена.", "family_id": family_id}
    return {
        "ok": True,
        "mapping": {
            "family_id": family_id,
            "revision": row[0],
            "parser_revision": row[1],
            "normalization_revision": row[2],
            "taxonomy": row[3],
            "source_timezone": row[4],
            "mappings": json.loads(row[5]),
            "evidence": json.loads(row[6]),
            "description": row[7],
            "created_at": row[8],
        },
    }


def get_parser_research(family_id: str, revision: int) -> dict:
    from schemas import FieldConstraint, GrokPattern, Response

    saved = get_family(family_id, revision)
    if not saved["ok"]:
        return saved
    family = saved["family"]
    family_constraints = [
        FieldConstraint.model_validate(rule).model_dump(mode="json")
        for rule in family["constraints"]
    ]
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT id, response_json, created_at FROM research_results ORDER BY id DESC"
        ).fetchall()
    for research_id, response_json, created_at in rows:
        payload = json.loads(response_json)
        if payload.get("type") == "normalization":
            continue
        try:
            response = Response.model_validate(payload)
        except Exception:
            continue
        parser = response.parser
        constraints = [rule.model_dump(mode="json") for rule in parser.constraints]
        record_schema = [field.model_dump(mode="json") for field in response.record_schema]
        if family["record_schema"] != record_schema or family_constraints != constraints:
            continue
        if parser.kind == "pipeline":
            matches = (
                family["stages"] == [stage.model_dump(mode="json") for stage in parser.stages]
                and family["patterns"] == []
                and family["layers"] == []
            )
        else:
            family_patterns = {
                pattern["name"]: GrokPattern.model_validate(pattern).model_dump(mode="json")
                for pattern in family["patterns"]
            }
            response_patterns = {
                pattern.name: pattern.model_dump(mode="json") for pattern in parser.patterns
            }
            matches = (
                family["layers"] == [layer.model_dump(mode="json") for layer in parser.layers]
                and family_patterns == response_patterns
                and family["stages"] == []
            )
        if matches:
            return {
                "ok": True,
                "research": {
                    "id": research_id,
                    "response": payload,
                    "created_at": created_at,
                },
            }
    return {
        "ok": False,
        "error": "not_found",
        "message": "Исходный research Response для этой parser revision не найден; используйте Family descriptions.",
        "family_id": family_id,
        "revision": revision,
    }


def save_research(response: dict) -> dict:
    with db_connect() as connection:
        cursor = connection.execute(
            "INSERT INTO research_results(response_json, created_at) VALUES (?, ?)",
            (json.dumps(response, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )
    return {"ok": True, "id": cursor.lastrowid}
