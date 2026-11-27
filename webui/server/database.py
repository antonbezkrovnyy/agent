from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

from .paths import RESEARCH, relative_path, resolve_workspace_path


TABLE_LABELS = {
    "parser_families": "Parser Families",
    "grok_patterns": "Grok-конструкции",
    "normalization_revisions": "Нормализация",
    "mapping_revisions": "MP SIEM mapping",
    "research_results": "Результаты исследований",
    "feedback": "Обратная связь моделей и runner",
    "taxonomy_fields": "Поля таксономии",
    "taxonomy_values": "Значения таксономии",
    "taxonomy_categories": "Категории таксономии",
    "semantic_embeddings_v2": "Семантический индекс",
}


def database_path(value: str) -> Path:
    path = resolve_workspace_path(value)
    if not path.is_relative_to(RESEARCH) or path.suffix.lower() != ".db":
        raise ValueError("Можно открыть только .db внутри research")
    if not path.is_file():
        raise FileNotFoundError(value)
    return path


@contextmanager
def readonly_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    try:
        yield connection
    finally:
        connection.close()


def parse_json_columns(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key, value in list(result.items()):
        if key.endswith("_json") and isinstance(value, str):
            if not value.strip():
                result[key.removesuffix("_json")] = []
            else:
                try:
                    result[key.removesuffix("_json")] = json.loads(value)
                except json.JSONDecodeError:
                    result[key.removesuffix("_json")] = value
            result.pop(key, None)
    return result


def compact(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= 1200 else value[:1200] + "…"
    if isinstance(value, list):
        items = [compact(item, depth + 1) for item in value[:30]]
        if len(value) > 30:
            items.append(f"… ещё {len(value) - 30}")
        return items
    if isinstance(value, dict):
        return {key: compact(item, depth + 1) for key, item in list(value.items())[:80]}
    if isinstance(value, bytes):
        return f"<binary: {len(value)} bytes>"
    return value


def list_databases() -> list[dict[str, Any]]:
    result = []
    for path in RESEARCH.rglob("*.db"):
        if not path.is_file():
            continue
        stat = path.stat()
        result.append({
            "path": relative_path(path),
            "name": path.stem,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        })
    return sorted(result, key=lambda item: item["modified_at"], reverse=True)


def database_overview(value: str) -> dict[str, Any]:
    path = database_path(value)
    with readonly_connection(path) as connection:
        names = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        tables = []
        for name in names:
            count = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')]
            tables.append({"name": name, "label": TABLE_LABELS.get(name, name), "count": count, "columns": columns})
    stat = path.stat()
    return {
        "path": relative_path(path),
        "name": path.stem,
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "tables": tables,
    }


def table_rows(
    database: str,
    table: str,
    page: int,
    page_size: int,
    search: str,
    sort: str | None,
    direction: str,
) -> dict[str, Any]:
    path = database_path(database)
    with readonly_connection(path) as connection:
        available = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )}
        if table not in available:
            raise ValueError("Неизвестная таблица")
        columns_info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        columns = [row[1] for row in columns_info]
        sort_column = sort if sort in columns else columns[0]
        order = "DESC" if direction.lower() == "desc" else "ASC"
        where = ""
        params: list[Any] = []
        if search:
            where = " WHERE " + " OR ".join(f'CAST("{column}" AS TEXT) LIKE ?' for column in columns)
            params = [f"%{search}%"] * len(columns)
        total = connection.execute(f'SELECT COUNT(*) FROM "{table}"{where}', params).fetchone()[0]
        rows = connection.execute(
            f'SELECT rowid AS __rowid__, * FROM "{table}"{where} '
            f'ORDER BY "{sort_column}" {order} LIMIT ? OFFSET ?',
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "table": table,
        "columns": ["__rowid__", *columns],
        "rows": [compact(parse_json_columns(row)) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


def table_record(database: str, table: str, rowid: int) -> dict[str, Any]:
    path = database_path(database)
    with readonly_connection(path) as connection:
        available = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )}
        if table not in available:
            raise ValueError("Неизвестная таблица")
        row = connection.execute(f'SELECT rowid AS __rowid__, * FROM "{table}" WHERE rowid = ?', (rowid,)).fetchone()
    if row is None:
        raise FileNotFoundError(str(rowid))
    return parse_json_columns(row)
