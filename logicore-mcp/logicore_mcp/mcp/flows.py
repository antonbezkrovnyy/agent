"""Оркестрация инструментов: валидация → трансляция → Earth → проекция (TD-0001, TD-0005, TD-0007, TD-0008)."""

import asyncio
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from mcp.types import ToolAnnotations

from logicore_mcp.domain.columns import RESERVED_COLUMNS, resolve_columns
from logicore_mcp.domain.errors import FilterError, LogicoreError, WindowError
from logicore_mcp.domain.filters import FieldSchema
from logicore_mcp.domain.projection import (
    build_activity,
    build_event_card,
    build_fill_rates_from_joint,
    build_search_response,
    build_top_values,
    truncate_message_rows,
)
from logicore_mcp.domain.window import align_bucket_grid, resolve_window
from logicore_mcp.earth.client import EarthClient
from logicore_mcp.earth.scheduler import unwrap
from logicore_mcp.earth.schemas import (
    build_count_request,
    build_events_request,
    build_groups_request,
    build_timeline_request,
)

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
ACTIVITY_LIMIT = 20
DISTRIBUTION_LIMIT = 100
TOP_VALUES_MAX_LIMIT = 20
TIMELINE_MAX_SPAN = timedelta(days=8)
MAX_BATCH_FIELDS = 10
FORBIDDEN_TOP_FIELDS = ("message", "timestamp")


def _error(exc: LogicoreError) -> dict[str, Any]:
    return {"ok": False, "error": str(exc), "hint": exc.hint}


UNEXPECTED_DEGRADE_HINT = (
    "Неожиданное исключение сервера (не ошибка Earth и не ошибка аргументов): "
    "повторите вызов; при стойком повторении сообщите владельцу — трейс в журнале stderr."
)


def _degrade_error(exc: BaseException) -> LogicoreError:
    """Любое исключение элемента batch — к ошибке деградации; имя класса всегда в тексте (PLAN-0007, TD-0008)."""
    if isinstance(exc, LogicoreError):
        return exc
    return LogicoreError(f"{type(exc).__name__}: {exc}", hint=UNEXPECTED_DEGRADE_HINT)


def _window_payload(start: datetime, end: datetime, source: str = "explicit") -> dict[str, str]:
    return {"from": start.isoformat(), "to": end.isoformat(), "source": source}


def _example_time_from(now: datetime | None = None) -> str:
    """Свежий пример time_from для dodge-подсказок: now−1ч, округлённый к минуте (TD-0021)."""
    moment = now or datetime.now(UTC)
    floored = moment.replace(second=0, microsecond=0) - timedelta(hours=1)
    return floored.strftime("%Y-%m-%dT%H:%M:%SZ")


def _window_error_with_example(exc: LogicoreError, tool: str, echo: Mapping[str, Any]) -> LogicoreError:
    """Rich hint «time_from обязателен»: готовый вызов с эхом уже переданных аргументов (TD-0021)."""
    if not (isinstance(exc, WindowError) and str(exc) == "time_from обязателен"):
        return exc
    example = {"time_from": _example_time_from(), **{key: value for key, value in echo.items() if value is not None}}
    return WindowError(
        "time_from обязателен",
        hint=f"Готовый вызов: {tool} {json.dumps(example, ensure_ascii=False)}. "
        "Укажите time_from (ISO-8601 с зоной) и повторите вызов; time_to опционален (по умолчанию сейчас).",
    )


def _window_or_raise(
    tool: str,
    time_from: str | None,
    time_to: str | None,
    echo: Mapping[str, Any],
) -> tuple[datetime, datetime, str]:
    """resolve_window с rich-hint обёрткой ошибки (TD-0021)."""
    try:
        return resolve_window(time_from=time_from, time_to=time_to)
    except LogicoreError as exc:
        raise _window_error_with_example(exc, tool=tool, echo=echo) from exc


def _iql_or_raise(filter: str | None) -> str | None:
    """Готовый IQL передаётся в MARS без промежуточной трансляции."""
    if filter is None:
        return None
    if not filter.strip():
        raise FilterError("iql должен быть непустой строкой", hint='Пример: "action = \'login\'".')
    return filter.strip()


def _unknown_field_error(name: str, schema: Mapping[str, FieldSchema], prefix: str) -> FilterError:
    """Ошибка неизвестного поля с похожими именами (TD-0008, TD-0013, TD-0014)."""
    from logicore_mcp.domain.filters import similar_fields

    similar = similar_fields(name, schema)
    hint = "Полный список — в resource logicore://schema/fields."
    if similar:
        hint = f"Похожие поля: {', '.join(similar)}. {hint}"
    return FilterError(f"{prefix}{name!r}", hint=hint)


def _iql_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _activity_iql(iql: str | None, groups: Sequence[Mapping[str, Any]] | None) -> str | None:
    conditions: list[str] = []
    for group in groups or ():
        column = str(group["column"])
        value = group.get("value")
        if value is None:
            conditions.append(f"{column} IS NULL")
        elif isinstance(value, list):
            conditions.append(f"{column} = [{', '.join(_iql_string(str(item)) for item in value)}]")
        else:
            conditions.append(f"{column} = {_iql_string(str(value))}")
    if iql:
        conditions.insert(0, f"({iql})")
    return " AND ".join(conditions) or None


async def _search_activity(
    client: EarthClient,
    *,
    iql: str | None,
    start: datetime,
    end: datetime,
    tenant_ids: list[int],
    has_rows: bool,
    groups: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Activity с теми же условиями, что и основная выдача поиска."""
    if not has_rows:
        return {"distinct": 0, "complete": True, "moments": []}
    groups_request = build_groups_request(
        columns=["timestamp"],
        filters=_activity_iql(iql, groups),
        time_from=start,
        time_to=end,
        tenant_ids=tenant_ids,
        limit=ACTIVITY_LIMIT,
    )
    try:
        return build_activity(unwrap(await client.group_by(groups_request))[0])
    except Exception:  # любой сбой activity — опустить её: строки поиска уже получены (PLAN-0007 п.3, TD-0008)
        logger.exception("tool=search_events activity failed")
        return None


def _validate_query_options(
    *,
    groups: Sequence[Mapping[str, Any]] | None,
    order_by: Sequence[Mapping[str, Any]] | None,
    query_limit: int | None,
    schema: Mapping[str, FieldSchema],
) -> None:
    if query_limit is not None and query_limit < 1:
        raise FilterError("query_limit должен быть >= 1", hint="Это общий потолок строк, не размер страницы.")
    for group in groups or ():
        column = str(group.get("column") or "")
        if column not in schema and column not in RESERVED_COLUMNS:
            raise _unknown_field_error(column, schema, prefix="Неизвестное поле groups: ")
    if order_by is not None and not order_by:
        raise FilterError("order_by не может быть пустым", hint="Уберите параметр или укажите хотя бы одну колонку.")
    for item in order_by or ():
        column = str(item.get("column") or "")
        if column not in schema and column not in RESERVED_COLUMNS:
            raise _unknown_field_error(column, schema, prefix="Неизвестное поле order_by: ")
        if item.get("action") not in ("ASC", "DESC"):
            raise FilterError("order_by.action должен быть ASC или DESC", hint='Пример: {"column":"timestamp","action":"DESC"}.')


def _prepare_search_args(
    *,
    page: int,
    limit: int,
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    columns: Sequence[str] | None,
    default_columns: Sequence[str],
    schema: Mapping[str, FieldSchema],
    groups: Sequence[Mapping[str, Any]] | None = None,
    order_by: Sequence[Mapping[str, Any]] | None = None,
    query_limit: int | None = None,
) -> tuple[datetime, datetime, str, tuple[str, ...], str | None, Sequence[Mapping[str, Any]] | None, Sequence[Mapping[str, Any]] | None, int | None]:
    """Проверяет окно, проекцию и нативные параметры поиска MARS."""
    if page < 1:
        raise FilterError("page должен быть >= 1", hint="Нумерация страниц с 1.")
    if limit < 1 or limit > MAX_LIMIT:
        raise FilterError(
            f"limit должен быть в диапазоне 1..{MAX_LIMIT}",
            hint=f"Дефолт {DEFAULT_LIMIT}, потолок {MAX_LIMIT}. Превышение — ошибка, не усечение.",
        )
    start, end, wsource = _window_or_raise(
        "search_events",
        time_from,
        time_to,
        echo={
            "iql": filter,
            "columns": list(columns) if columns is not None else None,
            "groups": list(groups) if groups is not None else None,
            "order_by": list(order_by) if order_by is not None else None,
            "query_limit": query_limit,
            "page": page if page > 1 else None,
            "limit": limit if limit != DEFAULT_LIMIT else None,
        },
    )
    columns_tuple = resolve_columns(columns, default_columns, schema)
    _validate_query_options(groups=groups, order_by=order_by, query_limit=query_limit, schema=schema)
    return start, end, wsource, columns_tuple, _iql_or_raise(filter), groups, order_by, query_limit


async def search_events_flow(
    client: EarthClient,
    *,
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    page: int,
    limit: int,
    schema: Mapping[str, FieldSchema],
    columns: Sequence[str] | None = None,
    default_columns: Sequence[str] = (),
    include_activity: bool = False,
    order: str = "desc",
    groups: Sequence[Mapping[str, Any]] | None = None,
    order_by: Sequence[Mapping[str, Any]] | None = None,
    query_limit: int | None = None,
) -> dict[str, Any]:
    """Пайплайн поиска с нативными groups/order/limit MARS."""
    try:
        start, end, wsource, columns_tuple, iql, groups, order_by, query_limit = _prepare_search_args(
            page=page,
            limit=limit,
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
    request = build_events_request(
        filters=iql,
        time_from=start,
        time_to=end,
        tenant_ids=tenant_ids,
        columns=columns_tuple,
        page=page,
        per_page=limit,
        order=order,
        groups=list(groups) if groups is not None else None,
        order_by=list(order_by) if order_by is not None else None,
        query_limit=query_limit,
    )
    try:
        response, coverage = unwrap(await client.search_events(request))
    except LogicoreError as exc:
        return _error(exc)
    logger.info("tool=search_events user=… rows={} total={}", len(response.data), response.meta.total)

    activity: dict[str, Any] | None = None
    if include_activity:
        activity = await _search_activity(
            client,
            iql=iql,
            start=start,
            end=end,
            tenant_ids=tenant_ids,
            has_rows=bool(response.data),
            groups=groups,
        )

    next_page_rows = None
    pagination_error = None
    if query_limit is None and len(response.data) >= limit:
        next_request = build_events_request(
            filters=iql,
            time_from=start,
            time_to=end,
            tenant_ids=tenant_ids,
            columns=columns_tuple,
            page=page + 1,
            per_page=limit,
            order=order,
            groups=list(groups) if groups is not None else None,
            order_by=list(order_by) if order_by is not None else None,
            query_limit=query_limit,
        )
        try:
            next_response, _ = unwrap(await client.search_events(next_request))
            next_page_rows = len(next_response.data)
        except Exception as exc:
            pagination_error = f"{type(exc).__name__}: {exc}"

    envelope = build_search_response(
        rows=truncate_message_rows(response.data),
        meta_total=response.meta.total,
        meta_schema=response.meta.schema_,
        requested_columns=columns_tuple,
        window=_window_payload(start, end, wsource),
        filter_applied=filter,
        activity=activity,
        order=order_by or order,
        page=page,
        per_page=limit,
        next_page_rows=next_page_rows,
    )
    if pagination_error:
        envelope["pagination_error"] = pagination_error
    if groups is not None:
        envelope["groups"] = list(groups)
    if query_limit is not None:
        envelope["query_limit"] = query_limit
        if (page - 1) * limit + len(response.data) >= query_limit:
            envelope["complete"] = False
            envelope["truncated"] = True
            envelope["has_more"] = False
            envelope["next_page"] = None
            envelope.pop("total", None)
            envelope["total_note"] = "Общее число скрыто query_limit; для полного окна уберите его."
            envelope["hint"] = f"Достигнут query_limit={query_limit}: результат ограничен запросом."
    if coverage is not None:
        envelope["coverage"] = coverage
        envelope["complete"] = False
    envelope["iql"] = iql
    return envelope


def _validate_top_field(field: str, schema: Mapping[str, FieldSchema]) -> None:
    """Общая валидация поля для top_values/top_values_batch: запрещённые и неизвестные (TD-0005, TD-0008)."""
    if field in FORBIDDEN_TOP_FIELDS:
        hints = {
            "message": "Текстовый поиск — search_events с IQL LIKE по message.",
            "timestamp": "Временную динамику смотрите в activity ответа search_events (include_activity: true).",
        }
        raise FilterError(f"Поле {field!r} недоступно для top_values", hint=hints[field])
    if field not in schema:
        raise _unknown_field_error(field, schema, prefix="Неизвестное поле: ")


async def count_events_flow(
    client: EarthClient,
    *,
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    schema: Mapping[str, FieldSchema],
    groups: Sequence[Mapping[str, Any]] | None = None,
    query_limit: int | None = None,
) -> dict[str, Any]:
    """Пайплайн подсчёта с нативными groups и общим query_limit MARS."""
    try:
        start, end, wsource = resolve_window(time_from=time_from, time_to=time_to)
    except LogicoreError as exc:
        return _error(
            _window_error_with_example(
                exc,
                tool="count_events",
                echo={"iql": filter, "groups": groups, "query_limit": query_limit},
            )
        )
    try:
        iql = _iql_or_raise(filter)
        _validate_query_options(groups=groups, order_by=None, query_limit=query_limit, schema=schema)
    except LogicoreError as exc:
        return _error(exc)

    await client.user_context()
    request = build_count_request(
        filters=iql,
        time_from=start,
        time_to=end,
        tenant_ids=client.tenant_ids(),
        groups=list(groups) if groups is not None else None,
        query_limit=query_limit,
    )
    try:
        count, coverage = unwrap(await client.count_events(request))
    except LogicoreError as exc:
        return _error(exc)
    logger.info("tool=count_events user=… count={}", count)
    span_minutes = (end - start).total_seconds() / 60
    envelope = {
        "ok": True,
        "count": count,
        "events_per_minute": round(count / span_minutes, 1) if span_minutes > 0 else None,
        "window": _window_payload(start, end, wsource),
        "filter_applied": filter,
        "iql": iql,
    }
    if groups is not None:
        envelope["groups"] = list(groups)
    if query_limit is not None:
        envelope["query_limit"] = query_limit
        envelope["complete"] = count < query_limit
        if count >= query_limit:
            envelope["hint"] = f"count достиг query_limit={query_limit}: это не полное число событий окна."
    if coverage is not None:
        envelope["coverage"] = coverage
        envelope["complete"] = False
    return envelope


async def timeline_flow(
    client: EarthClient,
    *,
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    schema: Mapping[str, FieldSchema],
    groups: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Пайплайн гистограммы с нативными groups MARS."""
    try:
        start, end, wsource = resolve_window(time_from=time_from, time_to=time_to)
    except LogicoreError as exc:
        return _error(_window_error_with_example(exc, tool="timeline", echo={"iql": filter, "groups": groups}))
    if end - start > TIMELINE_MAX_SPAN:
        return _error(
            FilterError(
                f"Окно timeline — до {TIMELINE_MAX_SPAN.days} дней",
                hint="Задачи Earth длиннее не завершаются на стенде; разбейте на части. Объём без ряда — count_events.",
            )
        )
    start, end = align_bucket_grid(start, end)
    try:
        iql = _iql_or_raise(filter)
        _validate_query_options(groups=groups, order_by=None, query_limit=None, schema=schema)
    except LogicoreError as exc:
        return _error(exc)

    await client.user_context()
    request = build_timeline_request(
        filters=iql,
        time_from=start,
        time_to=end,
        tenant_ids=client.tenant_ids(),
        groups=list(groups) if groups is not None else None,
    )
    try:
        result, coverage = unwrap(await client.timeline(request))
    except LogicoreError as exc:
        return _error(exc)

    raw_buckets = result.get("data")
    buckets: list[dict[str, Any]] = []
    for bucket in raw_buckets if isinstance(raw_buckets, list) else []:
        buckets.append(
            {
                "from": bucket.get("from"),
                "to": bucket.get("to"),
                "value": bucket.get("value"),
            }
        )
    granularity_seconds: int | None = None
    if len(buckets) >= 2 and buckets[0]["to"] and buckets[0]["from"]:
        try:
            from datetime import datetime

            first_from = datetime.strptime(str(buckets[0]["from"]), "%Y-%m-%d %H:%M:%S")
            first_to = datetime.strptime(str(buckets[0]["to"]), "%Y-%m-%d %H:%M:%S")
            granularity_seconds = int((first_to - first_from).total_seconds()) + 1
        except ValueError:
            granularity_seconds = None
    total = sum(bucket["value"] or 0 for bucket in buckets)
    logger.info("tool=timeline user=… buckets={} total={}", len(buckets), total)

    envelope = {
        "ok": True,
        "window": _window_payload(start, end, wsource),
        "filter_applied": filter,
        "iql": iql,
        "timeline": {
            "buckets": buckets,
            "bucket_count": len(buckets),
            "granularity_seconds": granularity_seconds,
            "total": total,
        },
    }
    if groups is not None:
        envelope["groups"] = list(groups)
    if result.get("status") == "partial":
        envelope["complete"] = False
        envelope["hint"] = "Поток timeline закрылся без done: результат неполный; повторите вызов или сузьте окно."
    if coverage is not None:
        envelope["coverage"] = coverage
        envelope["complete"] = False
    return envelope


async def aggregate_events_flow(
    client: EarthClient,
    *,
    dimensions: Sequence[str],
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    limit: int,
    schema: Mapping[str, FieldSchema],
) -> dict[str, Any]:
    """Группировка по составному ключу с явной нижней границей при потолке Earth."""
    try:
        unique: dict[str, None] = {}
        for name in dimensions:
            if not isinstance(name, str) or not name.strip():
                raise FilterError("Имя измерения должно быть непустой строкой")
            unique.setdefault(name, None)
        if not unique or len(unique) > 5:
            raise FilterError("dimensions: от 1 до 5 уникальных имён полей")
        for name in unique:
            if name not in schema and name not in RESERVED_COLUMNS:
                raise _unknown_field_error(name, schema, prefix="Неизвестное измерение: ")
        start, end, wsource = _window_or_raise(
            "aggregate_events",
            time_from,
            time_to,
            echo={"dimensions": list(unique), "iql": filter, "limit": limit},
        )
        iql = _iql_or_raise(filter)
    except LogicoreError as exc:
        return _error(exc)

    await client.user_context()
    request = build_groups_request(
        columns=list(unique),
        filters=iql,
        time_from=start,
        time_to=end,
        tenant_ids=client.tenant_ids(),
        limit=limit,
    )
    try:
        items, coverage = unwrap(await client.group_by(request))
    except LogicoreError as exc:
        return _error(exc)
    rows = [
        {
            **{name: item.get(name) for name in unique},
            "count": int(item.get("total_rows_count") or 0),
        }
        for item in items
    ]
    ceiling_reached = len(rows) >= limit
    complete = not ceiling_reached and coverage is None
    envelope: dict[str, Any] = {
        "ok": True,
        "window": _window_payload(start, end, wsource),
        "filter_applied": filter,
        "iql": iql,
        "dimensions": list(unique),
        "groups": rows,
        "groups_count": len(rows),
        "groups_lower_bound": len(rows),
        "groups_total": len(rows) if not ceiling_reached else None,
        "events_in_groups": sum(item["count"] for item in rows),
        "complete": complete,
        "ceiling_reached": ceiling_reached,
    }
    if ceiling_reached:
        envelope["hint"] = (
            f"Достигнут потолок {limit} групп: groups_total и точный COUNT DISTINCT неизвестны. "
            "Сузьте окно/фильтр или выполните полный анализ выгрузки через SQL."
        )
    if coverage is not None:
        envelope["coverage"] = coverage
        envelope["groups_total"] = None
    return envelope


async def top_values_flow(
    client: EarthClient,
    *,
    field: str,
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    limit: int,
    schema: Mapping[str, FieldSchema],
) -> dict[str, Any]:
    """Пайплайн топ-значений поля: full distribution → честные top/bottom (TD-0005, TD-0009)."""
    if not field or not field.strip():
        return _error(FilterError("field обязателен", hint="Имя поля — из resource logicore://schema/fields."))
    if limit < 1 or limit > TOP_VALUES_MAX_LIMIT:
        return _error(
            FilterError(f"limit должен быть в диапазоне 1..{TOP_VALUES_MAX_LIMIT}", hint="Дефолт 5, потолок 20.")
        )
    try:
        _validate_top_field(field, schema)
    except LogicoreError as exc:
        return _error(exc)
    try:
        start, end, wsource = resolve_window(time_from=time_from, time_to=time_to)
    except LogicoreError as exc:
        return _error(
            _window_error_with_example(
                exc,
                tool="top_values",
                echo={"field": field, "iql": filter, "limit": limit if limit != 5 else None},
            )
        )
    try:
        iql = _iql_or_raise(filter)
    except LogicoreError as exc:
        return _error(exc)

    await client.user_context()
    tenant_ids = client.tenant_ids()
    request = build_groups_request(
        columns=[field],
        filters=iql,
        time_from=start,
        time_to=end,
        tenant_ids=tenant_ids,
        limit=DISTRIBUTION_LIMIT,
    )
    try:
        items, coverage = unwrap(await client.group_by(request))
    except LogicoreError as exc:
        return _error(exc)
    logger.info("tool=top_values field={} items={}", field, len(items))

    response = build_top_values(field=field, items=items, limit=limit)
    response["window"] = _window_payload(start, end, wsource)
    response["iql"] = iql
    if coverage is not None:
        response["coverage"] = coverage
        response["complete"] = False
    return response


def _prepare_batch_args(
    *,
    fields: Sequence[str],
    filter: str | None,
    limit: int,
    time_from: str | None,
    time_to: str | None,
    schema: Mapping[str, FieldSchema],
) -> tuple[dict[str, None], datetime, datetime, str, str | None]:
    """Валидация аргументов batch: поля → лимит → схема полей → окно → фильтр (TD-0008/0009, TD-0021)."""
    unique: dict[str, None] = {}
    for field in fields:
        if not isinstance(field, str) or not field.strip():
            raise FilterError("Имя поля должно быть непустой строкой", hint="fields — список имён полей.")
        unique.setdefault(field, None)
    if not unique:
        example: dict[str, Any] = {
            "fields": ["action", "importance", "event_type"],
            "time_from": _example_time_from(),
        }
        if filter is not None:
            example["iql"] = filter
        if limit != 5:
            example["limit"] = limit
        raise FilterError(
            "Список fields пуст",
            hint=f"Готовый вызов: top_values_batch {json.dumps(example, ensure_ascii=False)}. "
            "fields обязателен (до 10 имён из logicore://schema/fields) — подставьте свои поля и повторите вызов.",
        )
    if len(unique) > MAX_BATCH_FIELDS:
        raise FilterError(
            f"Не больше {MAX_BATCH_FIELDS} полей",
            hint=f"Передано {len(unique)}. Разбейте на несколько вызовов.",
        )
    if limit < 1 or limit > TOP_VALUES_MAX_LIMIT:
        raise FilterError(f"limit должен быть в диапазоне 1..{TOP_VALUES_MAX_LIMIT}", hint="Дефолт 5, потолок 20.")
    for field in unique:
        _validate_top_field(field, schema)
    start, end, wsource = _window_or_raise(
        "top_values_batch",
        time_from,
        time_to,
        echo={"fields": list(unique), "iql": filter, "limit": limit if limit != 5 else None},
    )
    return unique, start, end, wsource, _iql_or_raise(filter)


def _batch_entries(unique: Mapping[str, None], results: Iterable[Any], limit: int) -> list[dict[str, Any]]:
    """Сводка batch: сбой поля — запись-ошибка, не падение тула (PLAN-0007 п.3, TD-0008)."""
    entries: list[dict[str, Any]] = []
    for field, result in zip(unique, results, strict=True):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            degraded = _degrade_error(result)  # не-LogicoreError тоже деградирует: имя класса всегда в тексте (F-1)
            logger.warning("tool=top_values_batch field={} degraded error={!r}", field, str(degraded))
            entries.append({"field": field, "error": str(degraded), "hint": degraded.hint})
            continue
        items, coverage = unwrap(result)  # широкие окна: group_by может вернуть PartialResult (живой кейс 2026-09-01)
        entry = build_top_values(field=field, items=items, limit=limit)
        if coverage:
            entry["coverage"] = coverage
            entry["complete"] = False
        entries.append(entry)
    return entries


async def top_values_batch_flow(
    client: EarthClient,
    *,
    fields: Sequence[str],
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    limit: int,
    schema: Mapping[str, FieldSchema],
) -> dict[str, Any]:
    """Пайплайн batch топ-значений: валидация всех полей → параллельные groups → сводка (TD-0008, TD-0009)."""
    try:
        unique, start, end, wsource, iql = _prepare_batch_args(
            fields=fields, filter=filter, limit=limit, time_from=time_from, time_to=time_to, schema=schema
        )
    except LogicoreError as exc:
        return _error(exc)

    await client.user_context()
    tenant_ids = client.tenant_ids()
    requests = [
        build_groups_request(
            columns=[field],
            filters=iql,
            time_from=start,
            time_to=end,
            tenant_ids=tenant_ids,
            limit=DISTRIBUTION_LIMIT,
        )
        for field in unique
    ]
    results = await asyncio.gather(*(client.group_by(request) for request in requests), return_exceptions=True)
    entries = _batch_entries(unique, results, limit)
    logger.info("tool=top_values_batch fields={} ok={}", len(unique), sum(1 for e in entries if e.get("ok")))
    envelope: dict[str, Any] = {
        "ok": True,
        "window": _window_payload(start, end, wsource),
        "filter_applied": filter,
        "iql": iql,
        "fields": entries,
    }
    return envelope


def now_utc() -> datetime:
    return datetime.now(UTC)


async def event_card_flow(client: EarthClient, *, uuid: str, timestamp: str) -> dict[str, Any]:
    """Пайплайн карточки: ключ uuid+timestamp из выборки → Earth single → конверт (TD-0006)."""
    if not uuid or not uuid.strip():
        return _error(FilterError("uuid обязателен", hint="Скопируйте uuid из строки выборки search_events."))
    if not timestamp or not timestamp.strip():
        return _error(
            FilterError(
                "timestamp обязателен",
                hint="Скопируйте точное значение timestamp из той же строки выборки: это момент события, не окно.",
            )
        )
    try:
        await client.user_context()
        response = await client.event_card(uuid=uuid, timestamp=timestamp, tenant_ids=client.tenant_ids())
    except LogicoreError as exc:
        return _error(exc)
    logger.info("tool=get_event uuid={} cards={}", uuid, len(response.data))
    if not response.data:
        return {
            "ok": True,
            "found": False,
            "uuid": uuid,
            "timestamp": timestamp,
            "hint": (
                "Пара uuid+timestamp не нашлась: живой uuid с чужим timestamp даёт тот же ответ, "
                "поэтому «событие удалено» — не вывод. Возьми точную пару из строки выборки "
                "search_events; известен только uuid — search_events с iql "
                "\"uuid = '<uuid>'\" на широком time_from и возьми timestamp из строки."
            ),
        }
    envelope = {"ok": True, "found": True, "uuid": uuid, "timestamp": timestamp}
    envelope.update(build_event_card(response.data[0]))
    return envelope


FILL_RATES_FIELDS_PER_REQUEST = 10
FILL_RATES_CONCURRENCY = 4
FILL_RATES_WAVE_PAUSE = 0.2
FILL_RATES_TIME_BUDGET = 40.0


def _prepare_fill_rates_args(
    *,
    offset: int,
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    schema: Mapping[str, FieldSchema],
) -> tuple[datetime, datetime, str, str | None]:
    """Валидация аргументов карты заполненности: курсор → окно → фильтр (TD-0010, TD-0021)."""
    if offset < 0:
        raise FilterError("offset должен быть >= 0", hint="Это курсор продолжения из предыдущего ответа.")
    start, end, wsource = _window_or_raise("field_fill_rates", time_from, time_to, echo={"iql": filter})
    return start, end, wsource, _iql_or_raise(filter)


async def _run_fill_rate_batches(
    client: EarthClient,
    *,
    batches: list[list[str]],
    iql: str | None,
    start: datetime,
    end: datetime,
    tenant_ids: list[int],
) -> tuple[list[tuple[list[str], list[dict[str, Any]] | LogicoreError, dict[str, Any] | None]], bool]:
    """Волны по FILL_RATES_CONCURRENCY батчей с бюджетом времени; сбой батча — деградация (TD-0010)."""
    semaphore = asyncio.Semaphore(FILL_RATES_CONCURRENCY)

    async def one_batch(
        batch: list[str],
    ) -> tuple[list[str], list[dict[str, Any]] | LogicoreError, dict[str, Any] | None]:
        request = build_groups_request(
            columns=batch,
            filters=iql,
            time_from=start,
            time_to=end,
            tenant_ids=tenant_ids,
            limit=DISTRIBUTION_LIMIT,
        )
        async with semaphore:
            try:
                rows, coverage = unwrap(await client.group_by(request))
                return batch, rows, coverage
            except Exception as exc:  # любой сбой батча — деградация полей, не падение тула (PLAN-0007 п.3, TD-0008)
                return batch, _degrade_error(exc), None

    results: list[tuple[list[str], list[dict[str, Any]] | LogicoreError, dict[str, Any] | None]] = []
    budget_spent = False
    loop = asyncio.get_running_loop()
    deadline = loop.time() + FILL_RATES_TIME_BUDGET
    for wave_start in range(0, len(batches), FILL_RATES_CONCURRENCY):
        wave = batches[wave_start : wave_start + FILL_RATES_CONCURRENCY]
        results.extend(await asyncio.gather(*(one_batch(batch) for batch in wave)))
        if wave_start + FILL_RATES_CONCURRENCY < len(batches):
            if loop.time() > deadline:
                budget_spent = True
                break
            await asyncio.sleep(FILL_RATES_WAVE_PAUSE)
    return results, budget_spent


def _collect_fill_results(
    results: list[tuple[list[str], list[dict[str, Any]] | LogicoreError, dict[str, Any] | None]],
    offset: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], int, int | None, str | None]:
    """Разбор батчей: заполненные/пустые/ошибочные поля, курсор и сигналы неполноты (TD-0010 рев. 2).

    Возвращает (filled, failed, empty, scanned, first_failed_offset, min_covered_until):
    first_failed_offset — offset первого сбойного батча для повтора тем же окном;
    min_covered_until — минимум covered_until частично покрытых батчей, None — покрытие полное.
    """
    filled: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    empty: list[str] = []
    scanned = 0
    first_failed_offset: int | None = None
    covered: list[str] = []
    for batch, result, batch_coverage in results:
        if isinstance(result, LogicoreError):
            if first_failed_offset is None:
                first_failed_offset = offset + scanned
            failed.extend({"field": field, "error": str(result), "hint": result.hint} for field in batch)
            scanned += len(batch)
            continue
        if batch_coverage is not None:
            until = batch_coverage.get("covered_until")
            if isinstance(until, str):
                covered.append(until)
        for entry in build_fill_rates_from_joint(batch, result):
            if entry["fill_rate"] > 0:
                filled.append(entry)
            else:
                empty.append(entry["field"])
        scanned += len(batch)
    filled.sort(key=lambda entry: (-entry["fill_rate"], entry["field"]))
    min_covered_until: str | None = min(covered, key=datetime.fromisoformat) if covered else None
    return filled, failed, empty, scanned, first_failed_offset, min_covered_until


async def field_fill_rates_flow(
    client: EarthClient,
    *,
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    schema: Mapping[str, FieldSchema],
    offset: int = 0,
) -> dict[str, Any]:
    """Пайплайн карты заполненности: составные ключи groups (10 полей/запрос), бюджет, курсор (TD-0010).

    Составной ключ сокращает число запросов ~в 10 раз: ~27 батчей на 262 поля,
    один вызов укладывается в бюджет 40 с (ниже клиентского таймаута MCP-хостов ~60 с).
    """
    try:
        start, end, wsource, iql = _prepare_fill_rates_args(
            offset=offset, time_from=time_from, time_to=time_to, filter=filter, schema=schema
        )
    except LogicoreError as exc:
        return _error(exc)
    if offset > len(schema):
        return _error(
            FilterError(
                f"offset={offset} больше числа полей схемы ({len(schema)})",
                hint=f"Используйте offset от 0 до {len(schema)}; значение берите из предыдущего ответа.",
            )
        )

    await client.user_context()
    tenant_ids = client.tenant_ids()
    fields = sorted(schema)[offset:]
    batches = [
        fields[i : i + FILL_RATES_FIELDS_PER_REQUEST] for i in range(0, len(fields), FILL_RATES_FIELDS_PER_REQUEST)
    ]
    results, budget_spent = await _run_fill_rate_batches(
        client, batches=batches, iql=iql, start=start, end=end, tenant_ids=tenant_ids
    )
    filled, failed, empty, scanned, first_failed_offset, min_covered_until = _collect_fill_results(results, offset)
    complete = not budget_spent and offset + scanned >= len(schema)
    next_offset = offset + scanned
    partial_batches = sum(1 for _, _, cov in results if cov is not None)
    logger.info(
        "tool=field_fill_rates offset={} scanned={} filled={} empty={} failed={} complete={} partial_batches={}",
        offset,
        scanned,
        len(filled),
        len(empty),
        len(failed),
        complete,
        partial_batches,
    )
    envelope: dict[str, Any] = {
        "ok": True,
        "window": _window_payload(start, end, wsource),
        "filter_applied": filter,
        "iql": iql,
        "fields_total": len(schema),
        "fields_scanned": scanned,
        "offset": next_offset,
        "complete": complete,
        "filled_fields": len(filled),
        "filled": filled,
        "empty": empty,
    }
    if failed:
        envelope["failed"] = failed
    if min_covered_until:
        envelope["time_coverage"] = {
            "complete": False,
            "covered_until": min_covered_until,
            "partial_batches": partial_batches,
        }
    hints: list[str] = []
    if not complete and budget_spent:
        hints.append(
            f"Просмотрено {next_offset} из {len(schema)} полей (бюджет времени). "
            f"Продолжите: field_fill_rates(offset={next_offset}) с тем же окном/фильтром."
        )
    if failed:
        retry = (
            f"повторите с тем же окном/фильтром offset={first_failed_offset}"
            if first_failed_offset is not None
            else "повторите вызов"
        )
        hints.append(
            f"Полей не измерено из-за сбоя Earth: {len(failed)} (см. failed) — {retry} "
            f"или проверьте точечно top_values по каждому полю. complete=true здесь — только курсор полей."
        )
    if min_covered_until:
        hints.append(
            f"Часть батчей покрыла окно лишь до {min_covered_until} (бюджет дробления): "
            f"числа заполненности относятся к префиксу окна, не всему окну."
        )
    if hints:
        envelope["hint"] = " ".join(hints)
    return envelope


DEFAULT_DUPLICATE_KEY = ("message",)
MAX_DUPLICATE_KEY_FIELDS = 5
DUPLICATES_MAX_LIMIT = 50


def _validate_duplicate_key(key_fields: Sequence[str] | None, schema: Mapping[str, FieldSchema]) -> dict[str, None]:
    """Ключ дубликата: непустые строки → уникализация → лимит → известность полей (TD-0013)."""
    source: Sequence[object] = key_fields if key_fields is not None else DEFAULT_DUPLICATE_KEY
    key: dict[str, None] = {}
    for name in source:
        if not isinstance(name, str) or not name.strip():
            raise FilterError("Ключ дубликата — непустые строки", hint="key_fields: имена полей из схемы.")
        key.setdefault(name, None)
    if not key:
        raise FilterError("Ключ дубликата пуст", hint="key_fields обязателен, дефолт — message.")
    if len(key) > MAX_DUPLICATE_KEY_FIELDS:
        raise FilterError(f"Не больше {MAX_DUPLICATE_KEY_FIELDS} полей в ключе", hint=f"Передано {len(key)}.")
    for name in key:
        if name not in schema and name not in RESERVED_COLUMNS:
            raise _unknown_field_error(name, schema, prefix="Неизвестное поле ключа: ")
    return key


async def duplicates_flow(
    client: EarthClient,
    *,
    time_from: str | None,
    time_to: str | None,
    filter: str | None,
    key_fields: Sequence[str] | None,
    limit: int,
    schema: Mapping[str, FieldSchema],
) -> dict[str, Any]:
    """Пайплайн дубликатов: groups по составному ключу → группы count>1 топом первым (TD-0013)."""
    if limit < 1 or limit > DUPLICATES_MAX_LIMIT:
        return _error(FilterError(f"limit должен быть в диапазоне 1..{DUPLICATES_MAX_LIMIT}", hint="Дефолт 10."))
    try:
        key = _validate_duplicate_key(key_fields, schema)
        start, end, wsource = _window_or_raise(
            "duplicates",
            time_from,
            time_to,
            echo={"key_fields": list(key), "iql": filter, "limit": limit if limit != 10 else None},
        )
        iql = _iql_or_raise(filter)
    except LogicoreError as exc:
        return _error(exc)

    await client.user_context()
    request = build_groups_request(
        columns=list(key),
        filters=iql,
        time_from=start,
        time_to=end,
        tenant_ids=client.tenant_ids(),
        limit=DISTRIBUTION_LIMIT,
    )
    try:
        items, coverage = unwrap(await client.group_by(request))
    except LogicoreError as exc:
        return _error(exc)
    multi = [
        {**{name: item.get(name) for name in key}, "count": int(item.get("total_rows_count") or 0)}
        for item in items
        if int(item.get("total_rows_count") or 0) > 1
    ]
    shown = multi[:limit]
    ceiling_reached = len(multi) >= DISTRIBUTION_LIMIT
    truncated = len(shown) < len(multi)
    logger.info("tool=duplicates key={} groups={} multi={}", list(key), len(items), len(multi))
    envelope: dict[str, Any] = {
        "ok": True,
        "window": _window_payload(start, end, wsource),
        "filter_applied": filter,
        "iql": iql,
        "key": list(key),
        "groups_scanned": len(items),
        "duplicate_groups": shown,
        "duplicate_groups_total": len(multi),
        "truncated": truncated,
    }
    if ceiling_reached:
        envelope["ceiling_reached"] = True
    if truncated:
        if ceiling_reached:
            envelope["hint"] = (
                f"Достигнут потолок {DISTRIBUTION_LIMIT} групп: неохваченные дубликаты возможны; "
                f"показаны первые {len(shown)} (limit {limit}). Сузьте окно/фильтр для полной картины."
            )
        else:
            envelope["hint"] = (
                f"Найдено {len(multi)} групп, показано {len(shown)} (limit {limit}): "
                f"поднимите limit (потолок 50) или сузьте окно/фильтр."
            )
    if coverage is not None:
        envelope["coverage"] = coverage
        envelope["complete"] = False
    return envelope


FIELD_LOOKUP_MAX_FIELDS = 20
_field_overlay_cache: dict[str, str] | None = None
_enum_values_cache: dict[str, list[dict[str, str]]] | None = None
ENUM_FIELDS = ("action", "object", "status", "subject", "event_src_category")


def _field_overlay() -> dict[str, str]:
    """Бандл overlay-описаний полей (справка PT SIEM 7.0), лениво, stdlib json (TD-0014)."""
    global _field_overlay_cache
    if _field_overlay_cache is None:
        from importlib.resources import files

        _field_overlay_cache = json.loads(
            files("logicore_mcp").joinpath("data/field_descriptions.json").read_text(encoding="utf-8")
        )
    return _field_overlay_cache


def _enum_values() -> dict[str, list[dict[str, str]]]:
    """Бандл перечислений таксономии (справка PT SIEM 7.0), лениво (TD-0015)."""
    global _enum_values_cache
    if _enum_values_cache is None:
        from importlib.resources import files

        _enum_values_cache = json.loads(
            files("logicore_mcp").joinpath("data/enum_values.json").read_text(encoding="utf-8")
        )
    return _enum_values_cache


def search_fields_flow(
    *,
    query: str,
    limit: int,
    schema: Mapping[str, FieldSchema],
) -> dict[str, Any]:
    """Поиск по живой схеме без обращения к событиям Earth."""
    from logicore_mcp.domain.filters import similar_fields

    overlay = _field_overlay()
    names = sorted(set(schema) | set(RESERVED_COLUMNS))
    needle = query.strip().lower()
    if needle:
        direct = [
            name for name in names
            if needle in name.lower() or needle in str(overlay.get(name) or "").lower()
        ]
        direct.sort(key=lambda name: (
            name.lower() != needle,
            not name.lower().startswith(needle),
            name,
        ))
        names = list(dict.fromkeys([*direct, *similar_fields(needle, schema, limit=5)]))
    entries = [
        {
            "field": name,
            "type": schema[name].type if name in schema else "DateTime('UTC')",
            "description": overlay.get(name),
        }
        for name in names[:limit]
    ]
    return {
        "ok": True,
        "query": query,
        "fields": entries,
        "returned": len(entries),
        "total": len(names),
        "truncated": len(names) > limit,
    }


async def field_lookup_flow(
    *,
    fields: Sequence[str],
    schema: Mapping[str, FieldSchema],
) -> dict[str, Any]:
    """Независимый lookup каждого поля: ошибка одного имени не скрывает остальные."""
    unique: dict[str, None] = {}
    for name in fields:
        if not isinstance(name, str) or not name.strip():
            return _error(FilterError("Имя поля должно быть непустой строкой", hint="fields — имена полей из схемы."))
        unique.setdefault(name, None)
    if not unique:
        return _error(
            FilterError(
                "Список fields пуст",
                hint='Готовый вызов: field_lookup {"fields": ["src_host", "event_src_hostname"]}.',
            )
        )
    if len(unique) > FIELD_LOOKUP_MAX_FIELDS:
        return _error(FilterError(f"Не больше {FIELD_LOOKUP_MAX_FIELDS} полей", hint=f"Передано {len(unique)}."))
    overlay = _field_overlay()
    entries: list[dict[str, Any]] = []
    invalid = 0
    for name in unique:
        if name not in schema and name not in RESERVED_COLUMNS:
            invalid += 1
            error = _unknown_field_error(name, schema, prefix="Неизвестное поле: ")
            entries.append({
                "field": name,
                "ok": False,
                "error": str(error),
                "hint": error.hint,
            })
            continue
        entries.append(
            {
                "field": name,
                "ok": True,
                "type": schema[name].type if name in schema else "DateTime('UTC')",
                "description": overlay.get(name),
            }
        )
    described = sum(1 for entry in entries if entry.get("description") is not None)
    logger.info("tool=field_lookup fields={} described={} invalid={}", len(entries), described, invalid)
    return {
        "ok": invalid < len(entries),
        "complete": invalid == 0,
        "fields": entries,
        "described": described,
        "without_overlay": sum(
            1 for entry in entries if entry.get("ok") and entry.get("description") is None
        ),
        "invalid": invalid,
        **({"error": "Все запрошенные поля неизвестны"} if invalid == len(entries) else {}),
    }


def enum_lookup_flow(*, field: str, value: str | None) -> dict[str, Any]:
    """Пайплайн расшифровки перечисления: словарь поля или точный lookup значения (TD-0015, R-0006)."""
    if field not in ENUM_FIELDS:
        return _error(
            FilterError(
                f"Поле {field!r} не является перечислением",
                hint=f"Enum-поля: {', '.join(ENUM_FIELDS)}.",
            )
        )
    entries = _enum_values().get(field, [])
    if value is None:
        logger.info("tool=enum_lookup field={} values={}", field, len(entries))
        return {"ok": True, "field": field, "values": entries}
    exact = next((entry for entry in entries if entry["value"] == value), None)
    if exact is not None:
        logger.info("tool=enum_lookup field={} value={} found", field, value)
        return {"ok": True, "field": field, "found": True, **exact}
    lowered = value.lower()
    candidates = [
        entry["value"] for entry in entries if lowered in entry["value"].lower() or entry["value"].lower() in lowered
    ][:5]
    hint = "Такого значения нет в таксономии PT SIEM 7.0 — расшифровку не выдумывать."
    if candidates:
        hint = f"Похожие значения: {', '.join(candidates)}. {hint}"
    logger.info("tool=enum_lookup field={} value={} not_found", field, value)
    return {"ok": True, "field": field, "found": False, "value": value, "hint": hint}
