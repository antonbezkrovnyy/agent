"""Наблюдаемый журнал исполнения вне checkpoints графа."""

import os
import threading
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
from langgraph.config import get_config, get_stream_writer
from psycopg.types.json import Jsonb

from memory_store import safe_json

_DATABASE_URL = os.environ.get("AGENT_MEMORY_DATABASE_URL", "")
_ready = False
_setup_lock = threading.Lock()


def _setup() -> None:
    global _ready
    if not _DATABASE_URL or _ready:
        return
    with _setup_lock:
        if _ready:
            return
        with psycopg.connect(_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS agent_memory")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS agent_memory.execution_events (
                        event_id TEXT PRIMARY KEY,
                        thread_id TEXT NOT NULL,
                        run_id TEXT NOT NULL DEFAULT '',
                        checkpoint_id TEXT NOT NULL DEFAULT '',
                        checkpoint_ns TEXT NOT NULL DEFAULT '',
                        graph_path TEXT NOT NULL DEFAULT '',
                        node TEXT NOT NULL DEFAULT '',
                        role TEXT NOT NULL DEFAULT '',
                        work_item_id TEXT NOT NULL DEFAULT '',
                        event_type TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ok',
                        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS execution_events_thread_idx
                    ON agent_memory.execution_events (thread_id, created_at, event_id)
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS execution_events_run_idx
                    ON agent_memory.execution_events (run_id, created_at, event_id)
                """)
        _ready = True


def _message_dict(message) -> dict:
    if hasattr(message, "model_dump"):
        return safe_json(message.model_dump(mode="json"))
    if isinstance(message, dict):
        return safe_json(message)
    return {"content": str(message)}


def audit_messages(role: str, state: dict, messages: list, tools: list | None = None) -> None:
    """Сохранить и стримить фактический вход модели без изменения выполнения."""
    system_prompt = ""
    if messages and getattr(messages[0], "type", None) == "system":
        content = messages[0].content
        system_prompt = content if isinstance(content, str) else str(content)
    event = {
        "event_id": uuid4().hex,
        "event_type": "model_called",
        "role": role,
        "node": role,
        "status": "ok",
        "work_item_id": str((state.get("active_work_item") or {}).get("id") or ""),
        "created_at": datetime.now(UTC).isoformat(),
        "payload": {
            "system_prompt": system_prompt,
            "messages": [_message_dict(message) for message in messages],
            "tools": [str(getattr(tool, "name", tool)) for tool in tools or []],
        },
    }
    try:
        configurable = get_config().get("configurable") or {}
    except RuntimeError:
        configurable = {}
    for key in ("thread_id", "run_id", "checkpoint_id", "checkpoint_ns"):
        event[key] = str(configurable.get(key) or "")
    event["graph_path"] = str(configurable.get("checkpoint_ns") or "")
    try:
        get_stream_writer()({"type": "execution_event", **event})
    except RuntimeError:
        pass
    if not _DATABASE_URL or not event["thread_id"]:
        return
    try:
        _setup()
        with psycopg.connect(_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO agent_memory.execution_events (
                        event_id, thread_id, run_id, checkpoint_id, checkpoint_ns,
                        graph_path, node, role, work_item_id, event_type, status, payload, created_at
                    ) VALUES (
                        %(event_id)s, %(thread_id)s, %(run_id)s, %(checkpoint_id)s, %(checkpoint_ns)s,
                        %(graph_path)s, %(node)s, %(role)s, %(work_item_id)s,
                        %(event_type)s, %(status)s, %(payload)s, %(created_at)s
                    )
                """, {**event, "payload": Jsonb(safe_json(event["payload"]))})
    except Exception:
        return


def list_execution_events(
    thread_id: str,
    *,
    run_id: str = "",
    role: str = "",
    event_type: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    if not _DATABASE_URL:
        return []
    _setup()
    clauses = ["thread_id = %s"]
    params: list = [thread_id]
    if run_id:
        clauses.append("run_id = %s")
        params.append(run_id)
    if role:
        clauses.append("role = %s")
        params.append(role)
    if event_type:
        clauses.append("event_type = %s")
        params.append(event_type)
    params.extend([max(1, min(limit, 500)), max(0, offset)])
    with psycopg.connect(_DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"""
                SELECT event_id, thread_id, run_id, checkpoint_id, checkpoint_ns,
                       graph_path, node, role, work_item_id, event_type, status, payload, created_at
                FROM agent_memory.execution_events
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at, event_id
                LIMIT %s OFFSET %s
            """, params)
            columns = [column.name for column in cursor.description]
            rows = cursor.fetchall()
    return [
        {**dict(zip(columns, row, strict=True)), "created_at": row[-1].isoformat()}
        for row in rows
    ]
