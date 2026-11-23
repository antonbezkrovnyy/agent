import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


def db_connect() -> sqlite3.Connection:
    path = Path(os.getenv("RESEARCH_DB", "research/log_research.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS grok_patterns (
            name TEXT PRIMARY KEY,
            definition TEXT NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS parser_families (
            family_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            patterns_json TEXT NOT NULL,
            layers_json TEXT NOT NULL,
            record_schema_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            family_description_json TEXT NOT NULL,
            parser_description_json TEXT NOT NULL,
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
    for column in ("family_description_json", "parser_description_json"):
        if column not in columns:
            connection.execute(
                f"ALTER TABLE parser_families ADD COLUMN {column} TEXT NOT NULL DEFAULT '{{}}'"
            )
    return connection


def save_grok(name: str, definition: str, description: str) -> dict:
    with db_connect() as connection:
        existing = connection.execute(
            "SELECT definition, description FROM grok_patterns WHERE name = ?", (name,)
        ).fetchone()
        if existing and existing != (definition, description):
            return {
                "ok": False,
                "error": "pattern_exists_with_different_definition",
                "message": "Выберите новое имя или получите существующее определение через get_grok_pattern.",
                "name": name,
            }
        if existing:
            return {"ok": True, "name": name, "status": "unchanged"}
        connection.execute(
            "INSERT INTO grok_patterns VALUES (?, ?, ?, ?)",
            (name, definition, description, datetime.now(timezone.utc).isoformat()),
        )
    return {"ok": True, "name": name, "status": "saved"}


def get_grok(name: str) -> dict:
    with db_connect() as connection:
        row = connection.execute(
            "SELECT name, definition, description, created_at FROM grok_patterns WHERE name = ?",
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
            "created_at": row[3],
        },
    }


def search_grok(query: str) -> dict:
    like = f"%{query}%"
    with db_connect() as connection:
        rows = connection.execute(
            "SELECT name, definition, description FROM grok_patterns "
            "WHERE name LIKE ? OR definition LIKE ? OR description LIKE ? LIMIT 20",
            (like, like, like),
        ).fetchall()
    return {
        "ok": True,
        "patterns": [
            {"name": row[0], "definition": row[1], "description": row[2]}
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
) -> dict:
    with db_connect() as connection:
        existing = connection.execute(
            "SELECT patterns_json, layers_json, record_schema_json, evidence_json, "
            "family_description_json, parser_description_json "
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
            "family_description_json, parser_description_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                "family_description_json, parser_description_json "
                "FROM parser_families WHERE family_id = ? ORDER BY revision DESC LIMIT 1",
                (family_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT revision, patterns_json, layers_json, record_schema_json, evidence_json, "
                "family_description_json, parser_description_json "
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
        },
    }


def save_research(response: dict) -> dict:
    with db_connect() as connection:
        cursor = connection.execute(
            "INSERT INTO research_results(response_json, created_at) VALUES (?, ?)",
            (json.dumps(response, ensure_ascii=False), datetime.now(timezone.utc).isoformat()),
        )
    return {"ok": True, "id": cursor.lastrowid}
