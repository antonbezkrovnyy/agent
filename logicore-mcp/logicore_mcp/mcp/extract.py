"""Постраничная выгрузка событий без участия LLM."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from logicore_mcp.domain.errors import LogicoreError
from logicore_mcp.domain.filters import FieldSchema, message_contains_substrings
from logicore_mcp.domain.projection import truncate_message_rows
from logicore_mcp.earth.client import EarthClient
from logicore_mcp.earth.scheduler import unwrap
from logicore_mcp.earth.schemas import build_events_request
from logicore_mcp.mcp.flows import _error, _prepare_search_args, _window_payload

PAGE_SIZE = 100


def _row_id(row: dict[str, Any]) -> str:
    return str(row.get("uuid") or json.dumps(row, sort_keys=True, ensure_ascii=False))


async def extract_data_flow(
    client: EarthClient,
    *,
    time_from: str | None,
    time_to: str | None,
    filter: Mapping[str, Any] | None,
    columns: Sequence[str] | None,
    max_rows: int,
    page: int,
    order: str,
    default_columns: Sequence[str],
    schema: Mapping[str, FieldSchema],
) -> dict[str, Any]:
    try:
        start, end, source, columns_tuple, iql = _prepare_search_args(
            page=page,
            limit=PAGE_SIZE,
            time_from=time_from,
            time_to=time_to,
            filter=filter,
            columns=columns,
            default_columns=default_columns,
            schema=schema,
        )
    except LogicoreError as exc:
        return _error(exc)

    await client.user_context()
    tenant_ids = client.tenant_ids()
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    complete = True
    next_page: int | None = None
    hint: str | None = None
    max_pages = (max_rows + PAGE_SIZE - 1) // PAGE_SIZE

    for current_page in range(page, page + max_pages):
        per_page = min(PAGE_SIZE, max_rows - len(events))
        request = build_events_request(
            filters=iql,
            time_from=start,
            time_to=end,
            tenant_ids=tenant_ids,
            columns=columns_tuple,
            page=current_page,
            per_page=per_page,
            order=order,
        )
        try:
            response, coverage = unwrap(await client.search_events(request))
        except LogicoreError as exc:
            if not events:
                return _error(exc)
            complete = False
            hint = str(exc)
            break
        rows = response.data
        if not rows:
            break
        row_ids = {_row_id(row) for row in rows}
        if row_ids.issubset(seen):
            complete = False
            next_page = None
            hint = f"Пагинация Earth повторила страницу {current_page}; выгрузка остановлена."
            break
        seen.update(row_ids)
        events.extend(truncate_message_rows(
            rows,
            match_substrings=message_contains_substrings(filter) if filter is not None else (),
        ))
        if coverage is not None:
            complete = False
            hint = "Earth покрыл только часть окна."
        if len(rows) < per_page:
            break
        next_page = current_page + 1
    else:
        if next_page is not None:
            complete = False
            hint = f"Достигнут max_rows={max_rows}; продолжите со следующей страницы."

    return {
        "ok": True,
        "window": _window_payload(start, end, source),
        "filter_applied": filter,
        "iql": iql,
        "columns": list(columns_tuple),
        "events": events,
        "returned_rows": len(events),
        "complete": complete,
        "next_page": next_page if not complete else None,
        **({"hint": hint} if hint else {}),
    }
