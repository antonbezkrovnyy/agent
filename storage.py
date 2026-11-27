from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
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
            target TEXT NOT NULL DEFAULT 'research',
            text_pattern TEXT,
            negative_evidence_json TEXT NOT NULL DEFAULT '[]',
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
        CREATE TABLE IF NOT EXISTS mp_exports (
            source TEXT NOT NULL,
            revision INTEGER NOT NULL,
            profile TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            spec_hash TEXT NOT NULL,
            yaml_path TEXT NOT NULL,
            conf_path TEXT NOT NULL,
            fixtures_path TEXT NOT NULL,
            report_path TEXT NOT NULL,
            native_ok INTEGER NOT NULL,
            rules_count INTEGER NOT NULL,
            positive_count INTEGER NOT NULL,
            negative_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (source, revision)
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key TEXT NOT NULL,
            source TEXT NOT NULL,
            agent TEXT NOT NULL,
            category TEXT NOT NULL,
            family_id TEXT,
            parser_revision INTEGER,
            normalization_revision INTEGER,
            mapping_revision INTEGER,
            field TEXT,
            summary TEXT NOT NULL,
            needed TEXT,
            evidence_json TEXT NOT NULL,
            details_json TEXT NOT NULL,
            blocking INTEGER NOT NULL,
            status TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1,
            resolution TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_dedup_open ON feedback(dedup_key, status);
        CREATE INDEX IF NOT EXISTS idx_feedback_family_status ON feedback(family_id, status);
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
    for definition in (
        "target TEXT NOT NULL DEFAULT 'research'",
        "text_pattern TEXT",
        "negative_evidence_json TEXT NOT NULL DEFAULT '[]'",
    ):
        column = definition.split()[0]
        if column not in columns:
            connection.execute(f"ALTER TABLE parser_families ADD COLUMN {definition}")
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
    target: str = "research",
    text_pattern: str | None = None,
    negative_evidence: list[str] | None = None,
) -> dict:
    with db_connect() as connection:
        existing = connection.execute(
            "SELECT patterns_json, layers_json, record_schema_json, evidence_json, "
            "family_description_json, parser_description_json, constraints_json, stages_json, "
            "target, text_pattern, negative_evidence_json "
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
            target,
            text_pattern,
            json.dumps(negative_evidence or [], ensure_ascii=False),
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
            "family_description_json, parser_description_json, constraints_json, stages_json, "
            "target, text_pattern, negative_evidence_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (family_id, revision, *payload, datetime.now(timezone.utc).isoformat()),
        )
    return {"ok": True, "family_id": family_id, "revision": revision, "status": "saved"}


def list_families() -> dict:
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT family_id, revision, evidence_json, family_description_json, target "
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
                "target": row[4],
            }
            for row in rows
        ],
    }


def list_parser_revisions() -> list[dict]:
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT family_id, revision, target FROM parser_families "
            "ORDER BY family_id, revision DESC"
        ).fetchall()
    return [{"family_id": row[0], "revision": row[1], "target": row[2]} for row in rows]


def get_family(family_id: str, revision: int | None = None) -> dict:
    with db_connect() as connection:
        if revision is None:
            row = connection.execute(
                "SELECT revision, patterns_json, layers_json, record_schema_json, evidence_json, "
                "family_description_json, parser_description_json, constraints_json, stages_json, "
                "target, text_pattern, negative_evidence_json "
                "FROM parser_families WHERE family_id = ? ORDER BY revision DESC LIMIT 1",
                (family_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT revision, patterns_json, layers_json, record_schema_json, evidence_json, "
                "family_description_json, parser_description_json, constraints_json, stages_json, "
                "target, text_pattern, negative_evidence_json "
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
            "target": row[9],
            "text_pattern": row[10],
            "negative_evidence": json.loads(row[11]),
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
        elif parser.kind == "mp_text":
            matches = (
                family.get("target") == "mp_text"
                and family.get("text_pattern") == parser.text_pattern
                and family.get("negative_evidence", []) == parser.negative_evidence
                and family["patterns"] == []
                and family["layers"] == []
                and family["stages"] == []
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


def save_mp_export(spec: dict, package: dict) -> dict:
    source = spec["source"]
    revision = spec["revision"]
    with db_connect() as connection:
        existing = connection.execute(
            "SELECT spec_hash FROM mp_exports WHERE source = ? AND revision = ?",
            (source, revision),
        ).fetchone()
        if existing and existing[0] != package["spec_hash"]:
            return {
                "ok": False,
                "error": "mp_export_revision_exists_with_different_content",
                "message": "Для изменённого MP candidate увеличьте revision.",
                "source": source,
                "revision": revision,
            }
        payload = (
            package["profile"],
            json.dumps(spec, ensure_ascii=False),
            package["spec_hash"],
            package["rules_path"],
            package["conf_path"],
            package["fixtures_path"],
            package["report_path"],
            int(package["native"]["ok"]),
            package["rules"],
            package["positive_fixtures"],
            package["negative_fixtures"],
            datetime.now(timezone.utc).isoformat(),
        )
        if existing:
            return {"ok": True, "source": source, "revision": revision, "status": "unchanged"}
        connection.execute(
            "INSERT INTO mp_exports (source, revision, profile, spec_json, spec_hash, yaml_path, "
            "conf_path, fixtures_path, report_path, native_ok, rules_count, positive_count, "
            "negative_count, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source, revision, *payload),
        )
    return {"ok": True, "source": source, "revision": revision, "status": "saved"}


def get_mp_export(source: str, revision: int | None = None) -> dict:
    with db_connect() as connection:
        if revision is None:
            row = connection.execute(
                "SELECT revision, profile, spec_json, spec_hash, yaml_path, conf_path, fixtures_path, "
                "report_path, native_ok, rules_count, positive_count, negative_count, created_at "
                "FROM mp_exports WHERE source = ? ORDER BY revision DESC LIMIT 1",
                (source,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT revision, profile, spec_json, spec_hash, yaml_path, conf_path, fixtures_path, "
                "report_path, native_ok, rules_count, positive_count, negative_count, created_at "
                "FROM mp_exports WHERE source = ? AND revision = ?",
                (source, revision),
            ).fetchone()
    if not row:
        return {"ok": False, "error": "not_found", "source": source, "revision": revision}
    return {
        "ok": True,
        "export": {
            "source": source,
            "revision": row[0],
            "profile": row[1],
            "spec": json.loads(row[2]),
            "spec_hash": row[3],
            "yaml_path": row[4],
            "conf_path": row[5],
            "fixtures_path": row[6],
            "report_path": row[7],
            "native_ok": bool(row[8]),
            "rules": row[9],
            "positive_fixtures": row[10],
            "negative_fixtures": row[11],
            "created_at": row[12],
        },
    }


FEEDBACK_COLUMNS = (
    "id",
    "source",
    "agent",
    "category",
    "family_id",
    "parser_revision",
    "normalization_revision",
    "mapping_revision",
    "field",
    "summary",
    "needed",
    "evidence_json",
    "details_json",
    "blocking",
    "status",
    "occurrences",
    "resolution",
    "created_at",
    "updated_at",
    "resolved_at",
)


def feedback_record(row: sqlite3.Row | tuple) -> dict:
    record = dict(zip(FEEDBACK_COLUMNS, row, strict=True))
    record["evidence"] = json.loads(record.pop("evidence_json"))
    record["details"] = json.loads(record.pop("details_json"))
    record["blocking"] = bool(record["blocking"])
    return record


def get_feedback(feedback_id: int) -> dict:
    with db_connect() as connection:
        row = connection.execute(
            f"SELECT {', '.join(FEEDBACK_COLUMNS)} FROM feedback WHERE id = ?",
            (feedback_id,),
        ).fetchone()
    if not row:
        return {
            "ok": False,
            "error": "not_found",
            "message": "Feedback не найден; проверьте id.",
            "id": feedback_id,
        }
    return {"ok": True, "feedback": feedback_record(row)}


def report_feedback(
    source: str,
    agent: str,
    category: str,
    summary: str,
    family_id: str | None = None,
    parser_revision: int | None = None,
    normalization_revision: int | None = None,
    mapping_revision: int | None = None,
    field: str | None = None,
    evidence: list[str] | None = None,
    details: object = None,
    blocking: bool = False,
    needed: str | None = None,
) -> dict:
    if source not in {"model", "runner"}:
        raise ValueError("source должен быть model или runner")
    if not agent.strip() or not category.strip() or not summary.strip():
        raise ValueError("agent, category и summary должны быть непустыми")
    evidence = list(dict.fromkeys(evidence or []))
    signature = {
        "source": source,
        "agent": agent,
        "category": category,
        "family_id": family_id,
        "parser_revision": parser_revision,
        "normalization_revision": normalization_revision,
        "mapping_revision": mapping_revision,
        "field": field,
        "summary": summary,
    }
    dedup_key = hashlib.sha256(
        json.dumps(signature, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        existing_row = connection.execute(
            f"SELECT {', '.join(FEEDBACK_COLUMNS)} FROM feedback "
            "WHERE dedup_key = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
            (dedup_key,),
        ).fetchone()
        if existing_row:
            existing = feedback_record(existing_row)
            feedback_id = existing["id"]
            connection.execute(
                "UPDATE feedback SET evidence_json = ?, details_json = ?, blocking = ?, "
                "needed = ?, occurrences = occurrences + 1, updated_at = ? WHERE id = ?",
                (
                    json.dumps(list(dict.fromkeys([*existing["evidence"], *evidence])), ensure_ascii=False),
                    json.dumps(details if details is not None else existing["details"], ensure_ascii=False, default=str),
                    int(blocking or existing["blocking"]),
                    needed or existing["needed"],
                    now,
                    feedback_id,
                ),
            )
        else:
            cursor = connection.execute(
                "INSERT INTO feedback ("
                "dedup_key, source, agent, category, family_id, parser_revision, "
                "normalization_revision, mapping_revision, field, summary, needed, "
                "evidence_json, details_json, blocking, status, occurrences, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 1, ?, ?)",
                (
                    dedup_key,
                    source,
                    agent,
                    category,
                    family_id,
                    parser_revision,
                    normalization_revision,
                    mapping_revision,
                    field,
                    summary,
                    needed,
                    json.dumps(evidence, ensure_ascii=False),
                    json.dumps(details, ensure_ascii=False, default=str),
                    int(blocking),
                    now,
                    now,
                ),
            )
            feedback_id = cursor.lastrowid
        row = connection.execute(
            f"SELECT {', '.join(FEEDBACK_COLUMNS)} FROM feedback WHERE id = ?",
            (feedback_id,),
        ).fetchone()
    return {
        "ok": True,
        "id": feedback_id,
        "deduplicated": existing_row is not None,
        "feedback": feedback_record(row),
    }


def list_feedback(
    status: str | None = "open",
    family_id: str | None = None,
    limit: int = 50,
) -> dict:
    if status not in {None, "open", "resolved", "accepted_uncertainty"}:
        raise ValueError("Неизвестный status feedback")
    if not 1 <= limit <= 200:
        raise ValueError("limit должен быть в диапазоне 1..200")
    clauses = []
    params = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if family_id is not None:
        clauses.append("family_id = ?")
        params.append(family_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with db_connect() as connection:
        rows = connection.execute(
            f"SELECT {', '.join(FEEDBACK_COLUMNS)} FROM feedback{where} "
            "ORDER BY updated_at DESC, id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return {
        "ok": True,
        "feedback": [feedback_record(row) for row in rows],
        "count": len(rows),
    }


def resolve_feedback(
    feedback_id: int,
    resolution: str,
    status: str = "resolved",
) -> dict:
    if status not in {"resolved", "accepted_uncertainty"}:
        raise ValueError("status должен быть resolved или accepted_uncertainty")
    if not resolution.strip():
        raise ValueError("resolution должен объяснять закрытие feedback")
    saved = get_feedback(feedback_id)
    if not saved["ok"]:
        return saved
    if saved["feedback"]["status"] != "open":
        return {
            "ok": False,
            "error": "feedback_already_closed",
            "message": "Feedback уже закрыт; новое проявление создаст отдельную запись.",
            "id": feedback_id,
            "feedback": saved["feedback"],
        }
    now = datetime.now(timezone.utc).isoformat()
    with db_connect() as connection:
        connection.execute(
            "UPDATE feedback SET status = ?, resolution = ?, updated_at = ?, resolved_at = ? WHERE id = ?",
            (status, resolution, now, now, feedback_id),
        )
    return get_feedback(feedback_id)
