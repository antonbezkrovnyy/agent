"""Предметное хранение оценок в собственной схеме agent_memory."""

import threading
from datetime import UTC, datetime

import psycopg
from psycopg.types.json import Jsonb

from models import EvaluationRecord


class EvaluationStore:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._ready = False
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.database_url)

    def _setup(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with psycopg.connect(self.database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("CREATE SCHEMA IF NOT EXISTS agent_memory")
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS agent_memory.run_evaluations (
                            run_id TEXT PRIMARY KEY,
                            thread_id TEXT NOT NULL,
                            trace_id TEXT NOT NULL DEFAULT '',
                            verdict TEXT NOT NULL,
                            score DOUBLE PRECISION NOT NULL,
                            summary TEXT NOT NULL DEFAULT '',
                            issues JSONB NOT NULL DEFAULT '[]'::jsonb,
                            metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
                            payload JSONB NOT NULL,
                            model TEXT NOT NULL DEFAULT '',
                            publication_error TEXT NOT NULL DEFAULT '',
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            published_at TIMESTAMPTZ
                        )
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS run_evaluations_recent_idx
                        ON agent_memory.run_evaluations (created_at DESC)
                    """)
            self._ready = True

    def has(self, run_id: str) -> bool:
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM agent_memory.run_evaluations WHERE run_id = %s",
                    (run_id,),
                )
                return cursor.fetchone() is not None

    def save(self, record: EvaluationRecord) -> None:
        self._setup()
        payload = record.model_dump(mode="json")
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO agent_memory.run_evaluations (
                        run_id, thread_id, trace_id, verdict, score, summary,
                        issues, metrics, payload, model, publication_error
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        trace_id = EXCLUDED.trace_id,
                        verdict = EXCLUDED.verdict,
                        score = EXCLUDED.score,
                        summary = EXCLUDED.summary,
                        issues = EXCLUDED.issues,
                        metrics = EXCLUDED.metrics,
                        payload = EXCLUDED.payload,
                        model = EXCLUDED.model,
                        publication_error = EXCLUDED.publication_error
                """, (
                    record.run_id,
                    record.thread_id,
                    record.trace_id,
                    record.verdict,
                    record.score,
                    record.summary,
                    Jsonb([item.model_dump(mode="json") for item in record.issues]),
                    Jsonb(record.metrics),
                    Jsonb(payload),
                    record.model,
                    record.publication_error,
                ))

    def mark_published(self, run_id: str) -> None:
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE agent_memory.run_evaluations SET published_at = %s WHERE run_id = %s",
                    (datetime.now(UTC), run_id),
                )

    def list_traced(self, limit: int = 100) -> list[tuple[str, str, str]]:
        """Оценённые runs с trace: (run_id, thread_id, trace_id), новые первыми."""
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT run_id, thread_id, trace_id
                    FROM agent_memory.run_evaluations
                    WHERE trace_id <> ''
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (limit,))
                return [(row[0], row[1], row[2]) for row in cursor.fetchall()]

    def summary(self) -> dict:
        self._setup()
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        count(*) AS total,
                        count(*) FILTER (WHERE trace_id = '') AS missing_traces,
                        count(*) FILTER (WHERE published_at IS NOT NULL) AS published
                    FROM agent_memory.run_evaluations
                """)
                row = cursor.fetchone()
        return {"total": row[0], "missing_traces": row[1], "published": row[2]}
