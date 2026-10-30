"""Планировщик Earth: адаптивное дробление окна по времени при таймаутах (TD-0029, PLAN-0009).

Декоратор над EarthClient (Protocol): оконные методы (search_events, count_events,
group_by, timeline) при таймауте Earth делят окно пополам и исполняют части строго
последовательно — вторая половина не ставится в очередь, пока не готова первая
(алгоритм владельца, PLAN-0009). Спуск рекурсивный в глубину; floor спана и общий
бюджет — НФТ LOGIQ_SPLIT_MIN_SPAN_S / LOGIQ_SPLIT_BUDGET_S. При исчерпании бюджета —
частичный результат с coverage{complete:false, covered_until, chunks_done, chunks_total}.

Все части идут через очередь клиента (TD-0028): каждый HTTP-переход уже сериализован.
"""

import copy
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from logicore_mcp.config import Settings
from logicore_mcp.domain.merge import (
    coverage_payload,
    merge_counts,
    merge_group_rows,
    merge_timeline_responses,
)
from logicore_mcp.earth.client import EarthClient, EarthTimeoutError
from logicore_mcp.earth.schemas import EventsResponse

_EARTH_FMT = "%Y-%m-%d %H:%M:%S"


def merge_events_responses(parts: list[Any], *, limit: int, order: str) -> EventsResponse:
    """Склейка EventsResponse-частей первой страницы (TD-0029 п. 5; слой earth: типизированные ответы).

    Строки конкатенируются и сортируются по timestamp (desc — новые сверху),
    срез до limit; total — сумма меток частей: служебное значение для флага
    truncated, в конверте при truncated ключ total опущен (TD-0004 п. 4);
    meta — от первой части с обновлённым total (schema идентичен по построению).
    """
    if not parts:
        raise ValueError("merge_events_responses: нет частей")
    first = parts[0]
    rows: list[Any] = []
    total = 0
    for response in parts:
        rows.extend(response.data)
        meta_total = response.meta.total
        total += meta_total if isinstance(meta_total, int) else len(response.data)
    rows.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=order == "desc")
    return EventsResponse(
        result=first.result,
        message=first.message,
        meta=first.meta.model_copy(update={"total": total}),
        data=rows[:limit],
    )


class PartialResult:
    """Частичный результат дробления: значение + coverage (TD-0029 п. 4, публичный API для flows)."""

    __slots__ = ("coverage", "value")

    def __init__(self, value: Any, coverage: dict[str, Any]) -> None:
        self.value = value
        self.coverage = coverage


def unwrap(value: Any) -> tuple[Any, dict[str, Any] | None]:
    """(значение, coverage|None): распаковка PartialResult; обычный результат — как есть (TD-0029)."""
    if isinstance(value, PartialResult):
        return value.value, value.coverage
    return value, None


def _with_window(request: dict[str, Any], start: datetime, end: datetime) -> dict[str, Any]:
    """Копия тела запроса с подменённым окном (deepcopy: мутировать исходник нельзя)."""
    chunk = copy.deepcopy(request)
    chunk["data"]["query"]["timestamp"]["fromTime"] = start.strftime(_EARTH_FMT)
    chunk["data"]["query"]["timestamp"]["toTime"] = end.strftime(_EARTH_FMT)
    return chunk


def _parse_earth_time(value: str) -> datetime:
    return datetime.strptime(value, _EARTH_FMT).replace(tzinfo=UTC)


def _window_of(request: dict[str, Any]) -> tuple[datetime, datetime]:
    ts = request["data"]["query"]["timestamp"]
    return _parse_earth_time(ts["fromTime"]), _parse_earth_time(ts["toTime"])


class ScheduledEarthClient:
    """Декоратор EarthClient: адаптивное дробление оконных методов при таймаутах (TD-0029)."""

    def __init__(self, inner: EarthClient, settings: Settings) -> None:
        self._inner = inner
        self._min_span_s = settings.split_min_span_s
        self._budget_s = settings.split_budget_s

    def __getattr__(self, name: str) -> Any:
        """Не-оконные методы и атрибуты — passthrough на внутренний клиент."""
        return getattr(self._inner, name)

    async def _execute(
        self,
        start: datetime,
        end: datetime,
        deadline: float,
        call: Callable[[datetime, datetime], Awaitable[Any]],
        combine: Callable[[list[Any]], Any],
    ) -> tuple[Any, dict[str, Any] | None]:
        """Спуск в глубину: try(full) → таймаут → пополам, сначала левая половина.

        Возвращает (результат, coverage|None): coverage != None только при
        исчерпанном бюджете (частичный результат, сплошное покрытие слева).
        """
        results: list[Any] = []
        covered_to = start
        chunks_done = 0
        chunks_total = 1
        stack: list[tuple[datetime, datetime]] = [(start, end)]
        while stack:
            w_start, w_end = stack.pop()
            span_s = (w_end - w_start).total_seconds()
            if time.monotonic() >= deadline:
                logger.info(
                    "split budget exhausted: covered_until={} chunks={}/{}",
                    covered_to,
                    chunks_done,
                    chunks_total,
                )
                return combine(results), coverage_payload(
                    covered_until=covered_to.isoformat(),
                    chunks_done=chunks_done,
                    chunks_total=chunks_total,
                )
            try:
                value = await call(w_start, w_end)
            except EarthTimeoutError:
                if span_s <= self._min_span_s:
                    raise
                mid = w_start + (w_end - w_start) / 2
                logger.info("split timeout: span={}s → {} | {}", int(span_s), mid, w_end)
                stack.append((mid, w_end))
                stack.append((w_start, mid))
                chunks_total += 1
                continue
            results.append(value)
            covered_to = w_end
            chunks_done += 1
        return combine(results), None

    async def _run(
        self,
        request: dict[str, Any],
        call: Callable[[datetime, datetime], Awaitable[Any]],
        combine: Callable[[list[Any]], Any],
    ) -> Any:
        start, end = _window_of(request)
        deadline = time.monotonic() + self._budget_s
        value, coverage = await self._execute(start, end, deadline, call, combine)
        if coverage is not None:
            return PartialResult(value, coverage)
        return value

    async def search_events(self, request: dict[str, Any]) -> Any:
        """Поиск с дроблением первой страницы (TD-0029: page>1 — без дробления)."""
        page = int(request.get("data", {}).get("pagination", {}).get("page") or 1)
        if page > 1:
            return await self._inner.search_events(request)

        async def call(w_start: datetime, w_end: datetime) -> Any:
            return await self._inner.search_events(_with_window(request, w_start, w_end))

        def combine(parts: list[Any]) -> Any:
            if len(parts) == 1:
                return parts[0]
            per_page = int(request["data"]["pagination"].get("perPage") or 20)
            order_by = request["data"]["query"].get("order") or []
            order = "desc"
            if order_by and str(order_by[0].get("action", "")).lower() != "desc":
                order = "asc"
            return merge_events_responses(parts, limit=per_page, order=order)

        return await self._run(request, call, combine)

    async def count_events(self, request: dict[str, Any]) -> Any:
        """Точный подсчёт: сумма count-частей (TD-0029 п. 5)."""

        async def call(w_start: datetime, w_end: datetime) -> Any:
            return await self._inner.count_events(_with_window(request, w_start, w_end))

        return await self._run(request, call, merge_counts)

    async def group_by(self, request: dict[str, Any]) -> Any:
        """Группировки (top_values/duplicates/fill_rates/activity): слияние распределений (TD-0029)."""

        async def call(w_start: datetime, w_end: datetime) -> Any:
            return await self._inner.group_by(_with_window(request, w_start, w_end))

        def combine(parts: list[Any]) -> Any:
            if len(parts) == 1:
                return parts[0]
            key_fields = tuple(request["data"]["query"].get("columns") or ())
            return merge_group_rows(parts, key_fields=key_fields)[0]

        return await self._run(request, call, combine)

    async def timeline(self, request: dict[str, Any]) -> Any:
        """Таймлайн: слияние бакетов с ребакетизацией (TD-0029; потолок 8 дней — в flows, TD-0018)."""

        async def call(w_start: datetime, w_end: datetime) -> Any:
            return await self._inner.timeline(_with_window(request, w_start, w_end))

        return await self._run(request, call, merge_timeline_responses)
