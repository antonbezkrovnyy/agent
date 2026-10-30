"""Выбор проекции колонок поиска: дефолт из конфигурации или своя, валидация по схеме (TD-0007)."""

from collections.abc import Mapping, Sequence

from logicore_mcp.domain.errors import FilterError
from logicore_mcp.domain.filters import FieldSchema, similar_fields

MAX_COLUMNS = 20

RESERVED_COLUMNS = frozenset({"timestamp"})


def resolve_columns(
    requested: Sequence[str] | None,
    default: Sequence[str],
    schema: Mapping[str, FieldSchema],
) -> tuple[str, ...]:
    """Эффективная проекция: None → дефолт; имена из схемы, без дубликатов, потолок MAX_COLUMNS (TD-0007).

    timestamp Earth принимает в колонках, но его нет в каталоге полей — зарезервированное имя.
    """
    if requested is None:
        source: Sequence[object] = default
    elif not requested:
        raise FilterError("Список columns пуст", hint="Уберите параметр columns — будет дефолтная проекция.")
    else:
        source = requested
    resolved: dict[str, None] = {}
    for name in source:
        if not isinstance(name, str):
            raise FilterError(
                "Имя колонки должно быть строкой", hint="columns — список имён полей из logicore://schema/fields."
            )
        resolved.setdefault(name, None)
    if len(resolved) > MAX_COLUMNS:
        raise FilterError(
            f"Не больше {MAX_COLUMNS} колонок",
            hint=f"Заказано {len(resolved)}. Сузьте проекцию.",
        )
    for name in resolved:
        if name not in schema and name not in RESERVED_COLUMNS:
            similar = similar_fields(name, schema)
            hint = "Полный список — в resource logicore://schema/fields."
            if similar:
                hint = f"Похожие поля: {', '.join(similar)}. {hint}"
            raise FilterError(f"Неизвестная колонка: {name!r}", hint=hint)
    return tuple(resolved)
