from functools import lru_cache
import hashlib
import json
import math
import os
import sqlite3
from datetime import datetime, timezone

from openai import OpenAI

import storage


FAMILY_LABELS = {
    "summary": "Summary",
    "event_semantics": "Event semantics",
    "envelope_and_payload": "Envelope and payload",
    "dispatch": "Dispatch",
    "applicability": "Applicability",
    "exclusions": "Exclusions",
}
PARSER_LABELS = {
    "summary": "Summary",
    "input_contract": "Input contract",
    "layering": "Layers",
    "output_contract": "Output contract",
    "revision_reason": "Revision reason",
    "regression_and_limitations": "Regression and limitations",
}


def model_name() -> str:
    return os.environ["EMBED_API_MODEL"]


@lru_cache(maxsize=1)
def get_client(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=120.0,
        max_retries=2,
    )


def encode(text: str, query: bool = False) -> list[float]:
    base_url = os.getenv("EMBED_API_BASE_URL") or os.environ["EMBED_API_BASE_UR"]
    client = get_client(base_url, os.environ["EMBED_API_KEY"])
    response = client.embeddings.create(model=model_name(), input=text)
    vector = list(response.data[0].embedding)
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        raise ValueError("embedding_provider_returned_zero_vector")
    return [value / norm for value in vector]


def description_text(description: dict, labels: dict[str, str]) -> str:
    return "\n".join(
        f"{label}: {description.get(key, '')}" for key, label in labels.items()
    )


def grok_document(row: sqlite3.Row | tuple) -> str:
    name, definition, description = row[:3]
    return f"Grok pattern: {name}\nDefinition: {definition}\nDescription: {description}"


def family_document(row: tuple) -> str:
    family_id, revision, evidence_json, description_json, layers_json, record_schema_json = row[:6]
    description = json.loads(description_json)
    if description:
        return f"Parser family: {family_id}\n" + description_text(description, FAMILY_LABELS)
    fields = json.loads(record_schema_json)
    return (
        f"Parser family: {family_id}@{revision}\n"
        f"Evidence: {json.loads(evidence_json)}\n"
        f"Layers: {json.loads(layers_json)}\n"
        f"Output fields: {[field.get('name') for field in fields]}"
    )


def parser_document(row: tuple) -> str:
    family_id, revision, evidence_json, _, layers_json, _, parser_description_json = row
    description = json.loads(parser_description_json)
    if description:
        return f"Parser revision: {family_id}@{revision}\n" + description_text(
            description, PARSER_LABELS
        )
    return (
        f"Parser revision: {family_id}@{revision}\n"
        f"Layers: {json.loads(layers_json)}\n"
        f"Evidence: {json.loads(evidence_json)}"
    )


def ensure_embedding(entity_type: str, entity_id: str, content: str) -> None:
    name = model_name()
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with storage.db_connect() as connection:
        existing = connection.execute(
            "SELECT content_hash, dimension FROM semantic_embeddings_v2 "
            "WHERE entity_type = ? AND entity_id = ? AND model_name = ?",
            (entity_type, entity_id, name),
        ).fetchone()
    if existing and existing[0] == content_hash and existing[1] > 0:
        return
    vector = encode(content)
    index_version = f"{name}:{len(vector)}"
    with storage.db_connect() as connection:
        connection.execute(
            "INSERT INTO semantic_embeddings_v2 ("
            "entity_type, entity_id, index_version, content, content_hash, model_name, "
            "dimension, vector_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(entity_type, entity_id, index_version) DO UPDATE SET "
            "content = excluded.content, content_hash = excluded.content_hash, "
            "dimension = excluded.dimension, vector_json = excluded.vector_json, "
            "created_at = excluded.created_at",
            (
                entity_type,
                entity_id,
                index_version,
                content,
                content_hash,
                name,
                len(vector),
                json.dumps(vector),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def index_grok(name: str) -> None:
    with storage.db_connect() as connection:
        row = connection.execute(
            "SELECT name, definition, description FROM grok_patterns WHERE name = ?", (name,)
        ).fetchone()
    if row:
        ensure_embedding("grok", name, grok_document(row))


def index_family(family_id: str, revision: int) -> None:
    with storage.db_connect() as connection:
        row = connection.execute(
            "SELECT family_id, revision, evidence_json, family_description_json, layers_json, "
            "record_schema_json, parser_description_json "
            "FROM parser_families WHERE family_id = ? AND revision = ?",
            (family_id, revision),
        ).fetchone()
    if not row:
        return
    entity_id = f"{family_id}@{revision}"
    ensure_embedding("family", entity_id, family_document(row))
    ensure_embedding("parser", entity_id, parser_document(row))


def search_embeddings(entity_type: str, query: str) -> list[dict]:
    query_vector = encode(query, query=True)
    with storage.db_connect() as connection:
        rows = connection.execute(
            "SELECT entity_id, vector_json FROM semantic_embeddings_v2 "
            "WHERE entity_type = ? AND model_name = ?",
            (entity_type, model_name()),
        ).fetchall()
    results = [
        {
            "entity_id": entity_id,
            "score": round(sum(left * right for left, right in zip(query_vector, json.loads(vector_json))), 6),
        }
        for entity_id, vector_json in rows
    ]
    return sorted(results, key=lambda item: item["score"], reverse=True)


def search_grok_semantic(query: str) -> dict:
    with storage.db_connect() as connection:
        rows = connection.execute(
            "SELECT name, definition, description FROM grok_patterns"
        ).fetchall()
    if not rows:
        return {"ok": True, "model": model_name(), "patterns": []}
    for row in rows:
        ensure_embedding("grok", row[0], grok_document(row))
    scores = {item["entity_id"]: item["score"] for item in search_embeddings("grok", query)}
    patterns = [
        {
            "name": row[0],
            "definition": row[1],
            "description": row[2],
            "score": scores[row[0]],
        }
        for row in rows
    ]
    patterns.sort(key=lambda item: item["score"], reverse=True)
    return {"ok": True, "model": model_name(), "patterns": patterns}


def search_families_semantic(query: str) -> dict:
    with storage.db_connect() as connection:
        rows = connection.execute(
            "SELECT family_id, revision, evidence_json, family_description_json, layers_json, "
            "record_schema_json FROM parser_families AS current "
            "WHERE revision = (SELECT MAX(revision) FROM parser_families "
            "WHERE family_id = current.family_id)"
        ).fetchall()
    if not rows:
        return {"ok": True, "model": model_name(), "families": []}
    for row in rows:
        entity_id = f"{row[0]}@{row[1]}"
        ensure_embedding("family", entity_id, family_document(row))
    scores = {item["entity_id"]: item["score"] for item in search_embeddings("family", query)}
    families = [
        {
            "family_id": row[0],
            "revision": row[1],
            "evidence": json.loads(row[2]),
            "description": json.loads(row[3]),
            "score": scores[f"{row[0]}@{row[1]}"],
        }
        for row in rows
    ]
    families.sort(key=lambda item: item["score"], reverse=True)
    return {"ok": True, "model": model_name(), "families": families}


def search_parsers_semantic(query: str) -> dict:
    with storage.db_connect() as connection:
        rows = connection.execute(
            "SELECT family_id, revision, evidence_json, family_description_json, layers_json, "
            "record_schema_json, parser_description_json FROM parser_families"
        ).fetchall()
    if not rows:
        return {"ok": True, "model": model_name(), "parsers": []}
    for row in rows:
        entity_id = f"{row[0]}@{row[1]}"
        ensure_embedding("parser", entity_id, parser_document(row))
    scores = {item["entity_id"]: item["score"] for item in search_embeddings("parser", query)}
    parsers = [
        {
            "family_id": row[0],
            "revision": row[1],
            "evidence": json.loads(row[2]),
            "description": json.loads(row[6]),
            "score": scores[f"{row[0]}@{row[1]}"],
        }
        for row in rows
    ]
    parsers.sort(key=lambda item: item["score"], reverse=True)
    return {"ok": True, "model": model_name(), "parsers": parsers}
