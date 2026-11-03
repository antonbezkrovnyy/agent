"""SQL-хранилище дел и Context Memory в отдельной базе агента."""

import json
import threading
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

from memory_models import CaseRecord, ContextFact


class MemoryStore:
    def __init__(self, database_url: str, embedding_dimensions: int):
        self.database_url = database_url
        self.embedding_dimensions = embedding_dimensions
        self._ready = False
        self._setup_lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.database_url)

    def _setup(self) -> None:
        if not self.enabled:
            return
        if self._ready:
            return
        with self._setup_lock:
            if self._ready:
                return
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    cursor.execute("CREATE SCHEMA IF NOT EXISTS agent_memory")
                    cursor.execute(f"""
                        CREATE TABLE IF NOT EXISTS agent_memory.investigations (
                            user_id TEXT NOT NULL,
                            investigation_id TEXT NOT NULL,
                            thread_id TEXT NOT NULL DEFAULT '',
                            task TEXT NOT NULL DEFAULT '',
                            summary TEXT NOT NULL DEFAULT '',
                            signature TEXT NOT NULL DEFAULT '',
                            entities JSONB NOT NULL DEFAULT '[]'::jsonb,
                            work_mode TEXT NOT NULL DEFAULT 'quick',
                            embedding vector({self.embedding_dimensions}),
                            snapshot JSONB NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            PRIMARY KEY (user_id, investigation_id)
                        )
                    """)
                    cursor.execute("""
                        ALTER TABLE agent_memory.investigations
                        ADD COLUMN IF NOT EXISTS signature TEXT NOT NULL DEFAULT ''
                    """)
                    cursor.execute("""
                        ALTER TABLE agent_memory.investigations
                        ADD COLUMN IF NOT EXISTS entities JSONB NOT NULL DEFAULT '[]'::jsonb
                    """)
                    cursor.execute(f"""
                        ALTER TABLE agent_memory.investigations
                        ADD COLUMN IF NOT EXISTS embedding vector({self.embedding_dimensions})
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS investigations_recent_idx
                        ON agent_memory.investigations (user_id, updated_at DESC)
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS investigations_embedding_idx
                        ON agent_memory.investigations
                        USING hnsw (embedding vector_cosine_ops)
                    """)
                    cursor.execute(f"""
                        CREATE TABLE IF NOT EXISTS agent_memory.context_facts (
                            id TEXT PRIMARY KEY,
                            scope TEXT NOT NULL,
                            statement TEXT NOT NULL,
                            category TEXT NOT NULL,
                            entities JSONB NOT NULL DEFAULT '[]'::jsonb,
                            status TEXT NOT NULL CHECK (status IN ('confirmed', 'observed')),
                            source_kind TEXT NOT NULL CHECK (source_kind IN ('user', 'investigation')),
                            source_case_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                            valid_from TIMESTAMPTZ,
                            valid_to TIMESTAMPTZ,
                            superseded_by TEXT,
                            embedding vector({self.embedding_dimensions}),
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS context_facts_active_idx
                        ON agent_memory.context_facts (scope, updated_at DESC)
                        WHERE valid_to IS NULL AND superseded_by IS NULL
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS context_facts_embedding_idx
                        ON agent_memory.context_facts
                        USING hnsw (embedding vector_cosine_ops)
                    """)
            self._ready = True

    def save_case(self, case: CaseRecord, embedding: list[float] | None = None) -> None:
        if not self.enabled:
            return
        self._setup()
        if embedding is not None:
            self._validate_embedding(embedding)
        payload = case.model_dump(mode="json")
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO agent_memory.investigations (
                        user_id, investigation_id, thread_id, task, summary,
                        signature, entities, work_mode, embedding, snapshot, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                    ON CONFLICT (user_id, investigation_id) DO UPDATE SET
                        thread_id = EXCLUDED.thread_id,
                        task = EXCLUDED.task,
                        summary = EXCLUDED.summary,
                        signature = EXCLUDED.signature,
                        entities = EXCLUDED.entities,
                        work_mode = EXCLUDED.work_mode,
                        embedding = EXCLUDED.embedding,
                        snapshot = EXCLUDED.snapshot,
                        updated_at = EXCLUDED.updated_at
                """, (
                    case.user_id,
                    case.investigation_id,
                    case.thread_id,
                    case.task,
                    case.summary,
                    case.signature,
                    Jsonb(case.entities),
                    case.work_mode,
                    self._vector(embedding) if embedding is not None else None,
                    Jsonb(payload),
                    case.updated_at,
                ))

    def get_case(self, user_id: str, investigation_id: str) -> dict | None:
        if not self.enabled:
            return None
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT snapshot FROM agent_memory.investigations
                    WHERE user_id = %s AND investigation_id = %s
                """, (user_id, investigation_id))
                row = cursor.fetchone()
        return CaseRecord.model_validate(row[0]).model_dump(mode="json") if row else None

    def recent_cases(self, user_id: str, limit: int) -> list[dict]:
        if not self.enabled:
            return []
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT snapshot FROM agent_memory.investigations
                    WHERE user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                """, (user_id, limit))
                rows = cursor.fetchall()
        return [CaseRecord.model_validate(row[0]).model_dump(mode="json") for row in rows]

    def search_cases(
        self,
        user_id: str,
        embedding: list[float],
        limit: int,
        similarity_threshold: float,
        exclude_id: str = "",
    ) -> list[dict]:
        if not self.enabled:
            return []
        self._setup()
        self._validate_embedding(embedding)
        vector = self._vector(embedding)
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT investigation_id, task, summary, signature, entities,
                           work_mode, updated_at, 1 - (embedding <=> %s::vector) AS similarity
                    FROM agent_memory.investigations
                    WHERE user_id = %s AND embedding IS NOT NULL
                      AND (%s = '' OR investigation_id <> %s)
                      AND 1 - (embedding <=> %s::vector) >= %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (vector, user_id, exclude_id, exclude_id, vector, similarity_threshold, vector, limit))
                rows = cursor.fetchall()
        columns = [
            "investigation_id", "task", "summary", "signature", "entities",
            "work_mode", "updated_at", "similarity",
        ]
        results = []
        for row in rows:
            item = dict(zip(columns, row, strict=False))
            item["updated_at"] = item["updated_at"].isoformat()
            item["similarity"] = float(row[-1])
            results.append(item)
        return results

    @staticmethod
    def _vector(value: list[float]) -> str:
        return json.dumps(value, separators=(",", ":"))

    def _validate_embedding(self, embedding: list[float]) -> None:
        if len(embedding) != self.embedding_dimensions:
            raise ValueError(
                f"Embedding has {len(embedding)} dimensions; expected {self.embedding_dimensions}"
            )

    def _write_fact(self, cursor, fact: ContextFact, embedding: list[float]) -> None:
        self._validate_embedding(embedding)
        cursor.execute("""
            INSERT INTO agent_memory.context_facts (
                id, scope, statement, category, entities, status, source_kind,
                source_case_ids, evidence_ids, valid_from, valid_to,
                superseded_by, embedding, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s
            )
            ON CONFLICT (id) DO UPDATE SET
                statement = EXCLUDED.statement,
                category = EXCLUDED.category,
                entities = EXCLUDED.entities,
                status = EXCLUDED.status,
                source_kind = EXCLUDED.source_kind,
                source_case_ids = EXCLUDED.source_case_ids,
                evidence_ids = EXCLUDED.evidence_ids,
                valid_from = EXCLUDED.valid_from,
                valid_to = EXCLUDED.valid_to,
                superseded_by = EXCLUDED.superseded_by,
                embedding = EXCLUDED.embedding,
                updated_at = EXCLUDED.updated_at
        """, (
            fact.id,
            fact.scope,
            fact.statement,
            fact.category,
            Jsonb([item.model_dump(mode="json") for item in fact.entities]),
            fact.status,
            fact.source_kind,
            Jsonb(fact.source_case_ids),
            Jsonb(fact.evidence_ids),
            fact.valid_from,
            fact.valid_to,
            fact.superseded_by,
            self._vector(embedding),
            fact.created_at,
            fact.updated_at,
        ))

    def put_fact(self, fact: ContextFact, embedding: list[float]) -> None:
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                self._write_fact(cursor, fact, embedding)

    def supersede_fact(self, target: ContextFact, fact: ContextFact, embedding: list[float]) -> None:
        self._setup()
        now = datetime.now(UTC)
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                self._write_fact(cursor, fact, embedding)
                cursor.execute("""
                    UPDATE agent_memory.context_facts
                    SET superseded_by = %s, valid_to = %s, updated_at = %s
                    WHERE id = %s
                """, (fact.id, fact.valid_from or now, now, target.id))

    def get_fact(self, scope: str, fact_id: str) -> ContextFact | None:
        if not self.enabled:
            return None
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT id, scope, statement, category, entities, status, source_kind,
                           source_case_ids, evidence_ids, valid_from, valid_to,
                           superseded_by, created_at, updated_at
                    FROM agent_memory.context_facts
                    WHERE scope = %s AND id = %s
                """, (scope, fact_id))
                row = cursor.fetchone()
        return self._fact_from_row(row) if row else None

    def find_fact_by_statement(self, scope: str, statement: str) -> ContextFact | None:
        if not self.enabled:
            return None
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT id, scope, statement, category, entities, status, source_kind,
                           source_case_ids, evidence_ids, valid_from, valid_to,
                           superseded_by, created_at, updated_at
                    FROM agent_memory.context_facts
                    WHERE scope = %s AND lower(statement) = lower(%s)
                      AND (valid_to IS NULL OR valid_to > now()) AND superseded_by IS NULL
                    ORDER BY updated_at DESC
                    LIMIT 1
                """, (scope, statement))
                row = cursor.fetchone()
        return self._fact_from_row(row) if row else None

    def search_facts(
        self,
        scope: str,
        embedding: list[float],
        limit: int,
        similarity_threshold: float,
    ) -> list[dict]:
        if not self.enabled:
            return []
        self._setup()
        self._validate_embedding(embedding)
        vector = self._vector(embedding)
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT id, scope, statement, category, entities, status, source_kind,
                           source_case_ids, evidence_ids, valid_from, valid_to,
                           superseded_by, created_at, updated_at,
                           1 - (embedding <=> %s::vector) AS similarity
                    FROM agent_memory.context_facts
                    WHERE scope = %s AND embedding IS NOT NULL
                      AND (valid_to IS NULL OR valid_to > now()) AND superseded_by IS NULL
                      AND 1 - (embedding <=> %s::vector) >= %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """, (vector, scope, vector, similarity_threshold, vector, limit))
                rows = cursor.fetchall()
        return [self._fact_from_row(row, include_similarity=True) for row in rows]

    @staticmethod
    def _fact_from_row(row, include_similarity: bool = False):
        columns = [
            "id", "scope", "statement", "category", "entities", "status",
            "source_kind", "source_case_ids", "evidence_ids", "valid_from",
            "valid_to", "superseded_by", "created_at", "updated_at",
        ]
        payload = dict(zip(columns, row, strict=False))
        fact = ContextFact.model_validate(payload)
        if include_similarity:
            return {**fact.model_dump(mode="json"), "similarity": float(row[-1])}
        return fact
