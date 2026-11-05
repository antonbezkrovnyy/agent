"""Merge результатов дробления окна: чистые функции над сырыми данными Earth (TD-0029, PLAN-0009).

Домен: без earth-/httpx-импортов (ADR-0001) — работаем плоскими структурами
(строки, распределения, бакеты). Типизированные ответы Earth (EventsResponse)
склеиваются в слое earth (scheduler.py). Все функции получают уже успешные
части — таймаут-части не существуют: спуск TD-0029 не пускает вторую половину,
пока не готова первая.
"""

from datetime import UTC, datetime
from typing import Any

EventRow = dict[str, Any]
GroupRow = dict[str, Any]


def merge_counts(counts: list[int]) -> int:
    """Сумма count-частей (TD-0029 п. 5: count — точная сумма)."""
    return sum(counts)


def _group_key(row: GroupRow, key_fields: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in key_fields)


def merge_group_rows(
    parts: list[list[GroupRow]],
    *,
    key_fields: tuple[str, ...],
) -> tuple[list[GroupRow], bool]:
    """Слияние распределений groups (TD-0029 п. 5).

    total_rows_count суммируется по составному ключу группировки; результирующий
    порядок — убывание суммарного count. Незаполненные (null) ключи не порождаются.
    Возвращает (rows, ок-ли-склейка): склейка честна всегда (суммы точны),
    полнота распределения определяется вызывающим (усечение/пропуск частей —
    его ответственность, здесь только арифметика).
    """
    merged: dict[tuple[Any, ...], GroupRow] = {}
    for rows in parts:
        for row in rows:
            key = _group_key(row, key_fields)
            if key not in merged:
                clone = {field: row.get(field) for field in key_fields}
                clone["total_rows_count"] = int(row.get("total_rows_count") or 0)
                merged[key] = clone
            else:
                merged[key]["total_rows_count"] += int(row.get("total_rows_count") or 0)
    ordered = sorted(merged.values(), key=lambda row: int(row.get("total_rows_count") or 0), reverse=True)
    return ordered, True


def merge_timeline_responses(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Объединяет SSE-части и ребакетизирует их по самой крупной сетке."""
    if not parts:
        raise ValueError("merge_timeline_responses: нет частей")

    def point(value: Any) -> datetime:
        text = str(value)
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))

    parsed: list[tuple[datetime, datetime, int]] = []
    for part in parts:
        for bucket in part.get("data") or []:
            parsed.append((point(bucket.get("from")), point(bucket.get("to")), int(bucket.get("value") or 0)))
    if not parsed:
        return {"status": "finished" if all(part.get("status") == "finished" for part in parts) else "partial", "data": []}
    coarse = max(max(int((end - start).total_seconds()) + 1, 1) for start, end, _ in parsed)
    acc: dict[int, int] = {}
    for start, _, value in parsed:
        epoch = int(start.timestamp())
        acc[epoch // coarse * coarse] = acc.get(epoch // coarse * coarse, 0) + value
    buckets = [
        {
            "from": datetime.fromtimestamp(start, UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "to": datetime.fromtimestamp(start + coarse - 1, UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "value": value,
        }
        for start, value in sorted(acc.items())
    ]
    return {
        "status": "finished" if all(part.get("status") == "finished" for part in parts) else "partial",
        "data": buckets,
    }


def coverage_payload(
    *,
    covered_until: str,
    chunks_done: int,
    chunks_total: int,
) -> dict[str, Any]:
    """Конверт покрытия при исчерпании бюджета дробления (TD-0029 п. 4, ревизия TD-0001).

    Спуск в глубину даёт сплошное покрытие слева: covered_until — правая граница
    последнего успешного куска от time_from.
    """
    return {
        "complete": False,
        "covered_until": covered_until,
        "chunks_done": chunks_done,
        "chunks_total": chunks_total,
    }
