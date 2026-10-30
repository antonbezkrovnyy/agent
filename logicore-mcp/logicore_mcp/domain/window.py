"""Окно времени поиска: time_from обязателен (required в схеме MCP, TD-0023), time_to опционален — TD-0021."""

from datetime import UTC, datetime, timedelta

from logicore_mcp.domain.errors import WindowError

SOURCE_EXPLICIT = "explicit"
SOURCE_TO_NOW = "to-now"


def _parse_instant(value: str, name: str) -> datetime:
    text = value.strip()
    if text.endswith(("z", "Z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WindowError(
            f"{name}: не ISO-8601 ({value!r})",
            hint="Формат: 2026-08-21T10:00:00Z или 2026-08-21T13:00:00+03:00.",
        ) from exc
    if moment.tzinfo is None:
        raise WindowError(
            f"{name}: отсутствует часовой пояс ({value!r})",
            hint="Укажите зону явно: Z (UTC) или смещение +03:00. Молчаливый дефолт исключён.",
        )
    return moment.astimezone(UTC)


def _now(now: datetime | None) -> datetime:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def resolve_window(
    *,
    time_from: str | None = None,
    time_to: str | None = None,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    """(start, end, source) в UTC: time_from обязателен, time_to опционален (TD-0021, TD-0023).

    time_to опущен → «сейчас» сервера, source="to-now" (конверт честно показывает охват);
    оба заданы → source="explicit". Dodge-вызов без time_from — ошибка до похода в Earth
    (доменный fallback: контрактный слой MCP обычно отсекает раньше, isError).
    """
    if not time_from or not time_from.strip():
        raise WindowError(
            "time_from обязателен",
            hint=(
                "ISO-8601 с зоной, напр. 2026-08-22T09:00:00Z. «Последние 15 минут» = "
                "time_from на 15 минут раньше; time_to можно опустить (по умолчанию сейчас)."
            ),
        )
    start = _parse_instant(time_from, "time_from")
    if time_to is None or not time_to.strip():
        end = _now(now)
        source = SOURCE_TO_NOW
    else:
        end = _parse_instant(time_to, "time_to")
        source = SOURCE_EXPLICIT
    if start >= end:
        raise WindowError("time_from должен быть раньше time_to", hint="Проверьте порядок границ окна.")
    return start, end, source


def server_time_payload(now: datetime | None = None) -> dict[str, object]:
    """«Сейчас» сервера для расчёта интервалов: то же время, из которого считаются to-now окна (TD-0026)."""
    moment = _now(now)
    return {"ok": True, "now": moment.isoformat(), "epoch": int(moment.timestamp())}


def align_bucket_grid(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Выравнивание границ окна до календарной сетки бакетов Earth (TD-0018, ревизия 2).

    Задачи /events/timeline с границами вне сетки бакетов не завершаются (квирк стенда):
    секунды — всегда; минуты — при суточных сетках 30м+; часы — при недельной сетке 3ч.
    Сетка по спану: <24ч — минута, <7 сут — час, далее — 3 часа.
    """
    span = end - start
    if span >= timedelta(days=7):
        grid = timedelta(hours=3)
        start_floor = _floor_grid(start, 3)
        end_floor = _floor_grid(end, 3)
    elif span >= timedelta(hours=24):
        grid = timedelta(hours=1)
        start_floor = start.replace(minute=0, second=0, microsecond=0)
        end_floor = end.replace(minute=0, second=0, microsecond=0)
    else:
        grid = timedelta(minutes=1)
        start_floor = start.replace(second=0, microsecond=0)
        end_floor = end.replace(second=0, microsecond=0)
    end_aligned = end_floor if end == end_floor else end_floor + grid
    return start_floor, max(end_aligned, start_floor + grid)


def _floor_grid(moment: datetime, step_hours: int) -> datetime:
    """Точка календарной сетки (0/3/6/…ч), не превосходящая момент (TD-0018)."""
    hour_point = moment.replace(minute=0, second=0, microsecond=0)
    return hour_point - timedelta(hours=hour_point.hour % step_hours)
