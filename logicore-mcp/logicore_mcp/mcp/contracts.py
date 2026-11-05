"""Контрактный слой инструментов: pydantic-валидаторы с русскими текстами (TD-0022, TD-0023).

Контрактные ошибки (диапазоны, длины, пустой time_from) уходят через isError SDK —
спец-путь SEP-1303; доменные ошибки (живая схема, IQL, Earth) остаются на конверте
RS-0002. Границы попадают в inputSchema через json_schema_extra (машиночитаемо),
рантайм-проверка — AfterValidator с русским текстом (без англ. pydantic-core).
time_from — БЕЗ дефолта: попадает в required inputSchema, генератор tool-call
обязан заполнить поле (TD-0023).
"""

from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field


def _non_empty_time_from(value: str) -> str:
    if not value.strip():
        raise ValueError(
            "time_from обязателен: ISO-8601 с зоной, напр. 2026-08-22T09:00:00Z. "
            'Готовый вызов: {"time_from": "2026-08-22T09:00:00Z"}.'
        )
    return value


def _non_empty_iql(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.strip():
        raise ValueError("iql должен быть непустой строкой, например \"action = 'login'\".")
    return value.strip()


def _search_limit(value: int) -> int:
    if not 1 <= value <= 100:
        raise ValueError("limit должен быть 1..100, дефолт 20. Превышение — ошибка, не усечение.")
    return value


def _top_values_limit(value: int) -> int:
    if not 1 <= value <= 20:
        raise ValueError("limit должен быть 1..20, дефолт 5.")
    return value


def _duplicates_limit(value: int) -> int:
    if not 1 <= value <= 50:
        raise ValueError("limit должен быть 1..50, дефолт 10.")
    return value


def _extract_max_rows(value: int | None) -> int | None:
    if value is not None and not 1 <= value <= 5000:
        raise ValueError("max_rows должен быть 1..5000, дефолт из конфига.")
    return value


def _bounded_items(max_items: int, error: str) -> Callable[[list[str] | None], list[str] | None]:
    def check(value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) > max_items:
            raise ValueError(error)
        return value

    return check


def _non_empty_str_field(value: str) -> str:
    if not value.strip():
        raise ValueError("field обязателен: имя поля из logicore://schema/fields.")
    return value


def _non_empty_fields(value: list[str]) -> list[str]:
    if not value or len(value) > 10:
        raise ValueError("fields обязателен: от 1 до 10 имён из logicore://schema/fields (дубликаты схлопываются).")
    return value


def _non_empty_lookup_fields(value: list[str]) -> list[str]:
    if not value:
        raise ValueError("fields обязателен: до 20 имён из logicore://schema/fields.")
    return value


def _order(value: str) -> str:
    if value not in ("asc", "desc"):
        raise ValueError('order должен быть "asc" или "desc", дефолт "desc" — свежие сверху.')
    return value


TIME_FROM_PARAM = Annotated[
    str,
    AfterValidator(_non_empty_time_from),
    Field(
        description=(
            "Начало окна, ОБЯЗАТЕЛЬНО: ISO-8601 с зоной, напр. 2026-08-21T10:00:00Z. "
            "Время — UTC: «сейчас» и локальное время человека сверяй через server_time, "
            "не прикидывай в уме. «Последние N минут» = now минус N."
        )
    ),
]

TIME_TO_PARAM = Annotated[
    str | None,
    Field(
        description=(
            "Конец окна, ISO-8601 с зоной (UTC; локальное время человека переводи через server_time). "
            "Пропущен — до текущего времени сервера (window.source = to-now). "
            "Сравниваете вызовы друг с другом — задавайте оба конца явно."
        )
    ),
]

FILTER_PARAM = Annotated[
    str | None,
    AfterValidator(_non_empty_iql),
]

FIELD_PARAM = Annotated[
    str,
    AfterValidator(_non_empty_str_field),
    Field(
        description=(
            "Поле распределения, обязательно: имя из logicore://schema/fields; "
            "message и timestamp запрещены. Смысл поля — field_lookup."
        )
    ),
]

FIELDS_PARAM = Annotated[
    list[str],
    AfterValidator(_non_empty_fields),
    Field(
        description=(
            "Обязательно: от 1 до 10 имён из logicore://schema/fields; message и timestamp "
            "запрещены; дубликаты схлопываются. Смысл полей — field_lookup."
        ),
        json_schema_extra={"minItems": 1, "maxItems": 10},
    ),
]

LOOKUP_FIELDS_PARAM = Annotated[
    list[str],
    AfterValidator(_non_empty_lookup_fields),
    Field(
        description="Обязательно: до 20 имён полей из logicore://schema/fields.",
        json_schema_extra={"minItems": 1, "maxItems": 20},
    ),
]

SEARCH_LIMIT = Annotated[
    int,
    AfterValidator(_search_limit),
    Field(
        description="Строк на страницу, 1..100, дефолт 20. Превышение — ошибка, не усечение.",
        json_schema_extra={"minimum": 1, "maximum": 100},
    ),
]

TOP_VALUES_LIMIT = Annotated[
    int,
    AfterValidator(_top_values_limit),
    Field(
        description="Сколько значений сверху и снизу, 1..20, дефолт 5.",
        json_schema_extra={"minimum": 1, "maximum": 20},
    ),
]

DUPLICATES_LIMIT = Annotated[
    int,
    AfterValidator(_duplicates_limit),
    Field(
        description="Групп в ответе, 1..50, дефолт 10.",
        json_schema_extra={"minimum": 1, "maximum": 50},
    ),
]

EXTRACT_MAX_ROWS = Annotated[
    int | None,
    AfterValidator(_extract_max_rows),
    Field(
        description="Максимум строк выгрузки, 1..5000. Если не задан — LOGIQ_EXTRACT_MAX_ROWS из конфига.",
        json_schema_extra={"minimum": 1, "maximum": 5000},
    ),
]

COLUMNS_PARAM = Annotated[
    list[str] | None,
    AfterValidator(_bounded_items(20, "columns: не больше 20 имён.")),
    Field(
        description=(
            "Своя проекция выборки: до 20 имён из logicore://schema/fields, дубликаты "
            "схлопываются. По умолчанию — серверная проекция. message допустим и усекается "
            "до 1000 символов (message_truncated: true); полный текст — через get_event."
        ),
        json_schema_extra={"maxItems": 20},
    ),
]

KEY_FIELDS_PARAM = Annotated[
    list[str] | None,
    AfterValidator(_bounded_items(5, "key_fields: не больше 5 имён в ключе дубликата.")),
    Field(
        description=(
            "Составной ключ дубликата: до 5 имён из logicore://schema/fields, timestamp "
            "разрешён. Дефолт [message] — точный повтор сырой строки; для нормализованных "
            "данных берите [generator, event_type, timestamp] или свой состав."
        ),
        json_schema_extra={"maxItems": 5},
    ),
]

ORDER_PARAM = Annotated[
    str,
    AfterValidator(_order),
    Field(
        description=(
            'Порядок строк по timestamp: "desc" (дефолт) — свежие сверху, "asc" — старейшие '
            "сверху. Свежесть потока: первая строка при desc — самое новое событие окна."
        )
    ),
]



class GroupFilter(BaseModel):
    column: str = Field(min_length=1, description="Поле из logicore://schema/fields.")
    value: str | list[str] | None = Field(
        default=None,
        description="Значение поля, список значений или null для IS NULL.",
    )


class OrderItem(BaseModel):
    column: str = Field(min_length=1, description="Поле сортировки из logicore://schema/fields.")
    action: Literal["ASC", "DESC"] = Field(description="Направление сортировки: ASC или DESC.")


GROUPS_PARAM = Annotated[
    list[GroupFilter] | None,
    Field(
        description=(
            "Дополнительные равенства MARS data.query.groups, объединяются по AND. "
            "Это не GROUP BY. Обычно условие проще записать прямо в iql."
        )
    ),
]

ORDER_BY_PARAM = Annotated[
    list[OrderItem] | None,
    Field(
        description=(
            "Полная сортировка MARS: [{column, action: ASC|DESC}], применяется по порядку. "
            "Если задано, заменяет простой order. Без него — timestamp + uuid."
        )
    ),
]

QUERY_LIMIT_PARAM = Annotated[
    int | None,
    Field(
        description=(
            "Общий потолок строк MARS data.query.limit, от 1. Это НЕ размер страницы: "
            "достижение потолка означает неполный результат и complete=false."
        ),
        json_schema_extra={"minimum": 1},
    ),
]
