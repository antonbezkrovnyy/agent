"""Проекция ответов Earth в конверты инструментов (TD-0001…TD-0010)."""

from typing import Any

from logicore_mcp.domain.errors import EarthError

ACTIVITY_CEILING = 20
TOP_VALUES_CEILING = 100
MESSAGE_PREVIEW_CHARS = 1000
TOTAL_NOTE = "выдача усечена лимитом; total недостоверен и опущен; точное число под условие — count_events"


def truncate_message_rows(rows: list[dict[str, Any]], cap: int = MESSAGE_PREVIEW_CHARS) -> list[dict[str, Any]]:
    """Возвращает заполненные поля строк и укорачивает message до preview-размера."""
    truncated: list[dict[str, Any]] = []
    for row in rows:
        clone = {key: value for key, value in row.items() if value is not None}
        message = clone.get("message")
        if isinstance(message, str) and len(message) > cap:
            clone["message"] = message[:cap]
            clone["message_truncated"] = True
        truncated.append(clone)
    return truncated


def resolve_total(events_count: int, meta_total: int | None) -> int:
    """total по TD-0004: пустая выдача → 0 (мусорный meta.total Earth не пробрасываем)."""
    if events_count == 0:
        return 0
    return meta_total if isinstance(meta_total, int) else events_count


def check_response_schema(meta_schema: list[dict[str, Any]] | None, requested_columns: tuple[str, ...]) -> None:
    """Контроль meta.schema против заказанных колонок — детектор дрейфа контракта (R-0001)."""
    if not meta_schema:
        return
    received = tuple(col.get("name") for col in meta_schema)
    if received != requested_columns:
        raise EarthError(
            "Контракт Earth изменился: колонки ответа не совпадают с заказанными",
            hint=f"Заказаны {requested_columns}, получены {received}. Зафиксируйте в reference/earth-api.md (R-0001).",
        )


def build_activity(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Распределение активности из groups(timestamp): моменты с count + полнота (TD-0005)."""
    moments = [
        {"at": item.get("timestamp"), "count": item.get("total_rows_count")}
        for item in items[:ACTIVITY_CEILING]
        if item.get("timestamp") is not None
    ]
    return {
        "distinct": len(items),
        "complete": len(items) < ACTIVITY_CEILING,
        "moments": moments,
    }


def build_search_response(
    *,
    rows: list[dict[str, Any]],
    meta_total: int | None,
    meta_schema: list[dict[str, Any]] | None,
    requested_columns: tuple[str, ...],
    window: dict[str, str],
    filter_applied: str | None,
    activity: dict[str, Any] | None = None,
    order: Any = "desc",
    page: int = 1,
    per_page: int | None = None,
) -> dict[str, Any]:
    """Конверт search_events с пагинацией по фактическому размеру страницы."""
    check_response_schema(meta_schema, requested_columns)
    total = resolve_total(len(rows), meta_total)
    has_more = per_page is not None and len(rows) >= per_page
    truncated = len(rows) < total or has_more
    response: dict[str, Any] = {
        "ok": True,
        "filter_applied": filter_applied,
        "order": order,
        "window": window,
        "truncated": truncated,
        "page": page,
        "has_more": has_more,
        "next_page": page + 1 if has_more else None,
        "events": rows,
    }
    if truncated:
        response["total_note"] = TOTAL_NOTE
    else:
        response["total"] = len(rows)
    if activity is not None:
        response["activity"] = activity
    return response


def build_event_card(card: dict[str, Any]) -> dict[str, Any]:
    """Карточка события: заполненные поля + счётчики, чтобы сброс пустых был явным (TD-0006, R-0004)."""
    filled = {key: value for key, value in card.items() if value is not None}
    return {
        "fields_total": len(card),
        "fields_filled": len(filled),
        "event": filled,
    }


def build_fill_rates_from_joint(batch: list[str], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill rates полей батча из составного распределения groups(batch): self-normalized приближение (TD-0010).

    null_count поля — сумма counts комбинаций, где поле null; fill_rate — к знаменателю
    суммы counts возвращённых комбинаций. Полное покрытие — точное; обрезка хвоста
    (len(rows) == лимиту) — приближение, достаточное для обнаружения заполненности.
    """
    total = sum(int(row.get("total_rows_count") or 0) for row in rows)
    entries: list[dict[str, Any]] = []
    for field in batch:
        null_count = sum(int(row.get("total_rows_count") or 0) for row in rows if row.get(field) is None)
        top_row = max(
            (row for row in rows if row.get(field) is not None),
            key=lambda row: int(row.get("total_rows_count") or 0),
            default=None,
        )
        entries.append(
            {
                "field": field,
                "fill_rate": round(1 - null_count / total, 4) if total else 0.0,
                "null_count": null_count,
                "top_value": top_row.get(field) if top_row is not None else None,
            }
        )
    return entries


def _dedup_key(value: Any) -> str:
    """Хешируемый ключ значения для дедупликации top/bottom: массивы и словари — репрезентацией (TD-0005)."""
    return repr(value)


def build_top_values(
    *,
    field: str,
    items: list[dict[str, Any]],
    limit: int,
) -> dict[str, Any]:
    """Топ/антитоп значений поля из groups(field): честный антитоп только при полном распределении (TD-0005).

    Каждый элемент top/bottom несёт share — % от суммы распределения (включая
    null-значения): модель не делит большие числа сама (TD-0024).
    """
    complete = len(items) < TOP_VALUES_CEILING
    ordered = sorted(items, key=lambda item: item.get("total_rows_count") or 0, reverse=True)
    total = sum(item.get("total_rows_count") or 0 for item in items)

    def _entry(item: dict[str, Any]) -> dict[str, Any]:
        count = item.get("total_rows_count")
        share = round(count / total * 100, 1) if total and isinstance(count, int) else None
        return {"value": item.get(field), "count": count, "share": share}

    top = [_entry(item) for item in ordered[:limit]]
    response: dict[str, Any] = {
        "ok": True,
        "field": field,
        "distinct": len(items),
        "complete": complete,
        "null_count": sum(1 for item in items if item.get(field) is None),
        "top": top,
    }
    if complete:
        top_keys = {_dedup_key(item.get(field)) for item in ordered[:limit]}
        tail = [item for item in ordered[-limit:] if _dedup_key(item.get(field)) not in top_keys]
        tail.reverse()
        response["bottom"] = [_entry(item) for item in tail]
    return response
