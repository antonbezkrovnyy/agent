"""Трансляция валидированного JsonLogic-дерева в IQL-строку Earth (TD-0003, TD-0004)."""

import math
from collections.abc import Mapping

from logicore_mcp.domain.errors import IqlError
from logicore_mcp.domain.filters import (
    FORBIDDEN_IN_STRINGS,
    FieldSchema,
    _typed_value,
    _var_node,
)


def _literal(value: object) -> str:
    """Рендер скаляра в IQL: строки в одинарных кавычках, числа/bool без (TD-0003)."""
    if isinstance(value, str):
        for char in FORBIDDEN_IN_STRINGS:
            if char in value:
                raise IqlError(f"Недопустимый символ {char!r} в строковом значении", hint=None)
        return f"'{value}'"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise IqlError("Числовое значение должно быть конечным", hint=None)
        return repr(value)
    raise IqlError(f"Неподдерживаемый литерал: {value!r}", hint=None)


def _render(node: Mapping, schema: Mapping[str, FieldSchema]) -> str:
    operator = next(iter(node))
    args = node[operator]
    if operator in ("and", "or"):
        if len(args) == 1:
            return _render(args[0], schema)
        joiner = " AND " if operator == "and" else " OR "
        return "(" + joiner.join(_render(arg, schema) for arg in args) + ")"
    if operator == "!":
        return f"NOT ({_render(args[0], schema)})"
    left, right = args
    name, field_schema = _var_node(left, schema)
    if operator in ("==", "!="):
        if right is None:
            return f"{name} IS NULL" if operator == "==" else f"{name} IS NOT NULL"
        literal = _literal(_typed_value(right, field_schema, operator, name))
        return f"{name} = {literal}" if operator == "==" else f"{name} != {literal}"
    if operator == "in":
        values = ", ".join(_literal(_typed_value(item, field_schema, operator, name)) for item in right)
        return f"{name} IN ({values})"
    if operator == "contains":
        literal = _literal(_typed_value(right, field_schema, operator, name))
        if field_schema.kind == "array":
            return f"has({name}, {literal})"
        return f"{name} LIKE '%{right}%'"
    symbol = {"<": "<", "<=": "<=", ">": ">", ">=": ">="}[operator]
    return f"{name} {symbol} {_literal(_typed_value(right, field_schema, operator, name))}"


def translate_filter(tree: Mapping, schema: Mapping[str, FieldSchema]) -> str:
    """Детерминированная трансляция валидированного дерева по таблице TD-0003/TD-0004.

    Вызывать после validate_filter: здесь только защита от запрещённых символов,
    полная проверка структуры не повторяется.
    """
    if not isinstance(tree, Mapping) or not tree:
        raise IqlError("Пустой фильтр", hint="Без фильтра — не передавайте его вовсе.")
    return _render(tree, schema)
