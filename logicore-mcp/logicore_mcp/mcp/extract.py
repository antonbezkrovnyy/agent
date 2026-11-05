"""Постраничная выгрузка событий без участия LLM."""

from collections.abc import Mapping, Sequence
from typing import Any

from logicore_mcp.domain.errors import LogicoreError
from logicore_mcp.domain.filters import FieldSchema
from logicore_mcp.domain.projection import truncate_message_rows
from logicore_mcp.earth.client import EarthClient
from logicore_mcp.earth.scheduler import unwrap
from logicore_mcp.earth.schemas import build_events_request
from logicore_mcp.mcp.flows import _error, _prepare_search_args, _window_payload

PAGE_SIZE = 100


async def extract_data_flow(
    client: EarthClient,
    *,
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    columns: Sequence[str] | None,
    max_rows: int,
    page: int,
    order: str,
    default_columns: Sequence[str],
    schema: Mapping[str, FieldSchema],
    groups: Sequence[Mapping[str, Any]] | None = None,
    order_by: Sequence[Mapping[str, Any]] | None = None,
    query_limit: int | None = None,
) -> dict[str, Any]:
    try:
        start, end, source, columns_tuple, iql, groups, order_by, query_limit = _prepare_search_args(
            page=page,
            limit=PAGE_SIZE,
            time_from=time_from,
            time_to=time_to,
            filter=filter,
            columns=columns,
            default_columns=default_columns,
            schema=schema,
            groups=groups,
            order_by=order_by,
            query_limit=query_limit,
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
        remaining = max_rows - len(events)
        if query_limit is not None:
            remaining = min(remaining, query_limit - len(events))
        if remaining <= 0:
            break
        per_page = min(PAGE_SIZE, remaining)
        request = build_events_request(
            filters=iql,
            time_from=start,
            time_to=end,
            tenant_ids=tenant_ids,
            columns=columns_tuple,
            page=current_page,
            per_page=per_page,
            order=order,
            groups=list(groups) if groups is not None else None,
            order_by=list(order_by) if order_by is not None else None,
            query_limit=query_limit,
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
        row_ids = {str(row["uuid"]) for row in rows if row.get("uuid") is not None}
        if row_ids and len(row_ids) == len(rows) and row_ids.issubset(seen):
            complete = False
            next_page = None
            hint = f"Пагинация Earth повторила страницу {current_page}; выгрузка остановлена."
            break
        seen.update(row_ids)
        events.extend(truncate_message_rows(rows))
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

    if query_limit is not None and len(events) >= query_limit:
        complete = False
        next_page = None
        hint = f"Достигнут query_limit={query_limit}: результат ограничен запросом."
    envelope = {
        "ok": True,
        "window": _window_payload(start, end, source),
        "filter_applied": filter,
        "iql": iql,
        "columns": list(columns_tuple),
        "order": order_by or order,
        "events": events,
        "returned_rows": len(events),
        "complete": complete,
        "next_page": next_page if not complete else None,
        **({"hint": hint} if hint else {}),
    }
    if groups is not None:
        envelope["groups"] = list(groups)
    if query_limit is not None:
        envelope["query_limit"] = query_limit
    return envelope
