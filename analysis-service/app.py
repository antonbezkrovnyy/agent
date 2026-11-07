import asyncio
import contextlib
import json
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

SERVICE_TOKEN = os.environ.get("ANALYSIS_SERVICE_TOKEN", "")
QUERY_TIMEOUT_SECONDS = float(os.environ.get("ANALYSIS_QUERY_TIMEOUT_SECONDS", "30"))
MAX_ROWS = int(os.environ.get("ANALYSIS_MAX_RESULT_ROWS", "10000"))
MEMORY_LIMIT = os.environ.get("ANALYSIS_MEMORY_LIMIT", "256MB")

app = FastAPI(title="LogiCore Analysis Service")
_executor = ThreadPoolExecutor(max_workers=2)
_semaphore = asyncio.Semaphore(2)
_forbidden = re.compile(
    r"\b(?:attach|copy|install|load|pragma|create|insert|update|delete|drop|alter|call|"
    r"read_csv|read_json|read_json_auto|read_parquet|read_text|glob|sqlite_scan|postgres_scan)\b",
    re.IGNORECASE,
)


class AnalysisRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    rows: list[dict[str, Any]]
    columns: list[str] = Field(default_factory=list)
    query: str = Field(min_length=1, max_length=20000)
    max_rows: int = Field(default=MAX_ROWS, ge=1, le=MAX_ROWS)


def validate_query(query: str) -> str:
    normalized = query.strip()
    if normalized.endswith(";"):
        normalized = normalized[:-1].rstrip()
    if not normalized.lower().startswith("select"):
        raise ValueError("Разрешён только один SELECT-запрос")
    if ";" in normalized or "--" in normalized or "/*" in normalized:
        raise ValueError("Несколько statements и комментарии в SQL запрещены")
    match = _forbidden.search(normalized)
    if match:
        raise ValueError(f"Запрещённая SQL-конструкция: {match.group(0)}")
    return normalized


def _available_columns(payload: AnalysisRequest) -> list[str]:
    return sorted(set(payload.columns) | {key for row in payload.rows for key in row})


def _query_error(exc: Exception, payload: AnalysisRequest) -> dict[str, Any]:
    message = f"{type(exc).__name__}: {exc}"
    missing = re.findall(r'Referenced column "([^"]+)" not found', message)
    if missing:
        return {
            "ok": False,
            "error": message,
            "error_code": "missing_column",
            "missing_columns": sorted(set(missing)),
            "available_columns": _available_columns(payload),
            "hint": "Исправьте SQL или создайте датасет с дополнительными columns; не повторяйте ту же выгрузку.",
        }
    if "Malformed JSON" in message or "unexpected character" in message:
        return {
            "ok": False,
            "error": message,
            "error_code": "invalid_literal",
            "available_columns": _available_columns(payload),
            "hint": "Строковые литералы в SQL записываются в одинарных кавычках, например action = 'deny'.",
        }
    if "memory limit" in message.lower() or "out of memory" in message.lower():
        return {
            "ok": False,
            "error": message,
            "error_code": "memory_limit",
            "hint": "Сузьте датасет или упростите SQL; увеличение выгрузки не поможет.",
        }
    return {"ok": False, "error": message, "error_code": "query_failed"}


def execute_query(connection, path: Path, query: str, max_rows: int, empty: bool) -> dict[str, Any]:
    connection.execute("PRAGMA threads=1")
    connection.execute(f"PRAGMA memory_limit='{MEMORY_LIMIT}'")
    relation = connection.read_json(str(path))
    if empty:
        relation = relation.filter("1 = 0")
    relation.create_view("events")
    started = time.monotonic()
    cursor = connection.execute(query)
    columns = [item[0] for item in cursor.description]
    values = cursor.fetchmany(max_rows + 1)
    truncated = len(values) > max_rows
    rows = [dict(zip(columns, row, strict=True)) for row in values[:max_rows]]
    return {
        "columns": columns,
        "rows": rows,
        "returned_rows": len(rows),
        "truncated": truncated,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


@app.middleware("http")
async def bearer_auth(request: Request, call_next):
    if SERVICE_TOKEN and request.url.path != "/health":
        if request.headers.get("authorization") != f"Bearer {SERVICE_TOKEN}":
            return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"ok": True, "service": "analysis-service"}


@app.post("/analyze")
async def analyze(payload: AnalysisRequest):
    try:
        query = validate_query(payload.query)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "error_code": "invalid_query"}
    rows = payload.rows
    if payload.columns:
        rows = [
            {**{name: None for name in payload.columns}, **row}
            for row in rows
        ]
    empty = not rows
    if empty and payload.columns:
        rows = [{name: None for name in payload.columns}]
    async with _semaphore:
        with tempfile.TemporaryDirectory(prefix="logicore-analysis-") as directory:
            path = Path(directory) / "dataset.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
                encoding="utf-8",
            )
            connection = duckdb.connect()
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                _executor, execute_query, connection, path, query, payload.max_rows, empty,
            )
            try:
                result = await asyncio.wait_for(asyncio.shield(future), timeout=QUERY_TIMEOUT_SECONDS)
            except TimeoutError:
                connection.interrupt()
                with contextlib.suppress(BaseException):
                    await future
                return {
                    "ok": False,
                    "error": f"SQL превысил технический таймаут {QUERY_TIMEOUT_SECONDS:g} с",
                    "error_code": "query_timeout",
                }
            except Exception as exc:
                return _query_error(exc, payload)
            finally:
                connection.close()
    return {
        "ok": True,
        "dataset_id": payload.dataset_id,
        "source_row_count": len(payload.rows),
        **result,
    }
