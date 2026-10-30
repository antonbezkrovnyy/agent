"""Whitelist JsonLogic-операторов и валидация дерева фильтра (TD-0003, TD-0004)."""

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from logicore_mcp.domain.errors import FilterError

BINARY_OPERATORS = frozenset({"==", "!=", "<", "<=", ">", ">="})
LIST_OPERATORS = frozenset({"and", "or"})
UNARY_OPERATORS = frozenset({"!"})
MEMBER_OPERATORS = frozenset({"in", "contains"})

OPERATORS = BINARY_OPERATORS | LIST_OPERATORS | UNARY_OPERATORS | MEMBER_OPERATORS

MAX_DEPTH = 8
MAX_NODES = 64

FORBIDDEN_IN_STRINGS = ("'", "\\")
FORBIDDEN_IN_CONTAINS = ("'", "\\", "%", "_")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


class FieldSchema(BaseModel):
    """Поле словаря Earth: имя + ClickHouse-тип из meta.schema (RS-0003)."""

    name: str
    type: str

    @property
    def base_type(self) -> str:
        stripped = self.type.strip()
        if stripped.startswith("Nullable(") and stripped.endswith(")"):
            return stripped[9:-1]
        return stripped

    @property
    def kind(self) -> str:
        """Класс значения для типизации фильтра: string | number | bool | datetime | array | other."""
        lowered = self.base_type.lower()
        if lowered.startswith("array(") and "string" in lowered:
            return "array"
        if lowered.startswith(("string", "uuid", "ipv4", "ipv6", "text")):
            return "string"
        if lowered.startswith(("int", "uint", "float", "decimal")):
            return "number"
        if lowered.startswith("bool"):
            return "bool"
        if lowered.startswith(("datetime", "date")):
            return "datetime"
        return "other"


def _edit_distance_within(a: str, b: str, ceiling: int) -> bool:
    """Дамерау-Левенштейн с ранним выходом: расстояние <= ceiling (для typo-матчинга)."""
    if abs(len(a) - len(b)) > ceiling:
        return False
    max_d = len(a) + len(b)
    da: dict[str, int] = {}
    d = [[max_d] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        db = 0
        for j in range(1, len(b) + 1):
            k = da.get(b[j - 1], 0)
            last = db
            if a[i - 1] == b[j - 1]:
                cost = 0
                db = j
            else:
                cost = 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if k > 0 and last > 0:
                d[i][j] = min(d[i][j], d[k - 1][last - 1] + (i - k - 1) + (j - last - 1) + 1)
        da[a[i - 1]] = i
        if min(d[i]) > ceiling:
            return False
    return d[len(a)][len(b)] <= ceiling


def similar_fields(name: str, schema: Mapping[str, FieldSchema], limit: int = 3) -> list[str]:
    """Похожие имена полей для ошибки «неизвестное поле» (альтпоток UC-0001).

    Ярусы: префикс → подстрока → поле-внутри-запроса → опечатка (Дамерау-Левенштейн ≤ 2).
    """
    lowered = name.lower()
    prefix = [f for f in schema if f.lower().startswith(lowered)]
    contains = [f for f in schema if lowered in f.lower() and f not in prefix]
    inside_query = [f for f in schema if f.lower() in lowered and f not in prefix and f not in contains]
    typo = [
        f
        for f in schema
        if f not in prefix
        and f not in contains
        and f not in inside_query
        and abs(len(f) - len(lowered)) <= 2
        and _edit_distance_within(lowered, f.lower(), 2)
    ]
    return (prefix + contains + inside_query + typo)[:limit]


def _field(name: Any, schema: Mapping[str, FieldSchema]) -> FieldSchema:
    if not isinstance(name, str):
        raise FilterError("Имя поля в var должно быть строкой", hint='Формат: {"var": "имя_поля"}.')
    if name not in schema:
        similar = similar_fields(name, schema)
        hint = "Полный список — в resource logicore://schema/fields."
        if similar:
            hint = f"Похожие поля: {', '.join(similar)}. {hint}"
        raise FilterError(f"Неизвестное поле фильтра: {name!r}", hint=hint)
    field_schema = schema[name]
    if field_schema.kind == "datetime":
        raise FilterError(
            f"Поле {name!r} — временнОе, фильтрация по нему не поддерживается",
            hint="Окно времени задавайте параметрами time_from/time_to или window.",
        )
    if field_schema.kind == "other":
        raise FilterError(
            f"Поле {name!r} имеет тип {field_schema.type!r} и не фильтруется",
            hint="Фильтрация поддержана для строковых, числовых, булевых полей и массивов (contains).",
        )
    return field_schema


def _typed_string(value: Any, field_schema: FieldSchema, operator: str, name: str) -> str:
    """Строковый/массивный литерал: тип + запрещённые символы IQL (TD-0003)."""
    if not isinstance(value, str):
        raise FilterError(
            f"Поле {name!r} строковое, получен {type(value).__name__}",
            hint=f"Ожидается строка (тип {field_schema.type}).",
        )
    forbidden = FORBIDDEN_IN_CONTAINS if operator == "contains" else FORBIDDEN_IN_STRINGS
    for char in forbidden:
        if char in value:
            raise FilterError(
                f"Недопустимый символ {char!r} в значении поля {name!r}",
                hint="Экранирование IQL не верифицировано: ' \\ % _ (для contains) запрещены.",
            )
    return value


def _typed_number(value: Any, field_schema: FieldSchema, name: str) -> int | float:
    """Численный литерал: int/float/строка-число со строгим парсингом (TD-0003)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise FilterError(
            f"Поле {name!r} числовое, получен {type(value).__name__}",
            hint=f"Ожидается число (тип {field_schema.type}); строка допускается, если строго число.",
        )
    if isinstance(value, str):
        if not _NUMERIC_RE.match(value):
            raise FilterError(
                f"Значение {value!r} поля {name!r} не является числом",
                hint=f"Строгий парсинг int/float (тип {field_schema.type}).",
            )
        return float(value) if "." in value else int(value)
    return value


def _typed_value(value: Any, field_schema: FieldSchema, operator: str, name: str) -> Any:
    """Приведение литерала к типу поля с явными ошибками (TD-0003)."""
    kind = field_schema.kind
    if value is None:
        if operator in ("==", "!="):
            return None
        raise FilterError(
            f"null допустим только в ==/!=, получен в {operator!r} по полю {name!r}",
            hint='NULL-семантика: {"==": [{{"var": ...}}, null]} / {"!=": [...]}}.',
        )
    if kind in ("string", "array"):
        return _typed_string(value, field_schema, operator, name)
    if kind == "number":
        return _typed_number(value, field_schema, name)
    if kind == "bool":
        if not isinstance(value, bool):
            raise FilterError(
                f"Поле {name!r} булево, получен {type(value).__name__}",
                hint="Ожидается true/false.",
            )
        return value
    raise FilterError(f"Поле {name!r} не фильтруется", hint=None)


def _var_node(node: Any, schema: Mapping[str, FieldSchema]) -> tuple[str, FieldSchema]:
    if not isinstance(node, Mapping) or set(node) != {"var"}:
        raise FilterError(
            "Левый операнд оператора — только имя поля",
            hint='Формат: {"оператор": [{"var": "имя_поля"}, значение]}.',
        )
    name = node["var"]
    return name, _field(name, schema)


def _validate_binary(operator: str, args: Any, schema: Mapping[str, FieldSchema]) -> None:
    """Бинарный оператор: var-левый, семантика массивов/contains, тип правого (TD-0003/0004/0012)."""
    if not isinstance(args, list) or len(args) != 2:
        raise FilterError(
            f"Оператор {operator!r} требует ровно два операнда",
            hint=f'Пример: {{"{operator}": [{{"var": "поле"}}, значение]}}.',
        )
    left, right = args
    name, field_schema = _var_node(left, schema)
    if field_schema.kind == "array" and operator != "contains":
        raise FilterError(
            f"Поле {name!r} — массив, фильтрация только contains",
            hint=(
                'Массив-поля: {"contains": [{"var": "tags"}, "тег"]} — Has(тег); '
                '«НЕ содержит»: {"!": [{"contains": [{"var": "tags"}, "тег"]}]} — NOT Has(тег). '
                "Оператор in к массив-полям неприменим (это «поле IN (значения)» для скаляров)."
            ),
        )
    if operator == "contains" and field_schema.kind not in ("string", "array"):
        raise FilterError(
            f"contains применим только к строковым полям и массивам, {name!r} — {field_schema.type}",
            hint='Текстовый поиск по message: {"contains": [{"var": "message"}, "подстрока"]}.',
        )
    if operator == "in":
        if not isinstance(right, list) or not right:
            raise FilterError(
                f"Правый операнд in по полю {name!r} — непустой список значений",
                hint='Пример: {"in": [{"var": "action"}, ["start", "elevate"]]}.',
            )
        for item in right:
            if item is None:
                raise FilterError(
                    f"null внутри списка in по полю {name!r} не поддерживается",
                    hint="NULL-семантика — только в ==/!=.",
                )
            _typed_value(item, field_schema, operator, name)
        return
    _typed_value(right, field_schema, operator, name)


def _nodes_hint(root_op: str) -> str:
    if root_op == "and":
        return "Сократите цепочку AND: объедините значения через in, сузьте окно, уберите наименее селективные условия."
    return "Сузьте окно или упростите вложенность AND/OR/NOT."


def _validate_node(
    node: Any, schema: Mapping[str, FieldSchema], depth: int, counter: list[int], root_op: str | None = None
) -> None:
    counter[0] += 1
    if counter[0] > MAX_NODES:
        raise FilterError(
            f"Фильтр превышает лимит {MAX_NODES} узлов",
            hint=_nodes_hint(root_op or ""),
        )
    if depth > MAX_DEPTH:
        raise FilterError(
            f"Фильтр глубже {MAX_DEPTH} уровней",
            hint="Упростите вложенность AND/OR/NOT.",
        )
    if not isinstance(node, Mapping) or len(node) != 1:
        raise FilterError(
            "Узел фильтра — объект ровно с одним оператором",
            hint='Формат JsonLogic: {"and": [ ... ]}, {"==": [ ... ]} и т.п.',
        )
    operator = next(iter(node))
    if operator not in OPERATORS:
        raise FilterError(
            f"Оператор {operator!r} не поддерживается",
            hint=f"Whitelist: {', '.join(sorted(OPERATORS))}.",
        )
    args = node[operator]
    if operator in LIST_OPERATORS:
        if not isinstance(args, list) or len(args) < 1:
            raise FilterError(
                f"Оператор {operator!r} требует непустой список операндов",
                hint='Пример: {"and": [условие1, условие2]}; одиночный операнд допустим (вырожденный and/or).',
            )
        for arg in args:
            _validate_node(arg, schema, depth + 1, counter, root_op)
        return
    if operator in UNARY_OPERATORS:
        if not isinstance(args, list) or len(args) != 1:
            raise FilterError("Оператор '!' требует ровно один операнд", hint='Пример: {"!": [условие]}.')
        _validate_node(args[0], schema, depth + 1, counter, root_op)
        return
    _validate_binary(operator, args, schema)


def validate_filter(tree: Mapping, schema: Mapping[str, FieldSchema]) -> None:
    """Полная валидация JsonLogic-дерева: whitelist, арности, лимиты, поля, типы (TD-0003, TD-0004)."""
    if not isinstance(tree, Mapping):
        raise FilterError("Фильтр — объект JsonLogic", hint='Пример: {"==": [{"var": "action"}, "start"]}.')
    root_op = next(iter(tree)) if tree else None
    _validate_node(tree, schema, depth=1, counter=[0], root_op=root_op)


def _message_contains_literal(node: Mapping) -> str | None:
    """Литерал contains по message, если узел — такой contains; иначе None (TD-0030)."""
    if set(node) != {"contains"} or not isinstance(node["contains"], list) or len(node["contains"]) != 2:
        return None
    left, right = node["contains"]
    if (
        isinstance(left, Mapping)
        and set(left) == {"var"}
        and left["var"] == "message"
        and isinstance(right, str)
        and right
    ):
        return right
    return None


def _child_walk_plan(node: Mapping, in_or: bool, skip: bool) -> list[tuple[Any, bool, bool]]:
    """План обхода детей узла: пары (ребёнок, in_or, skip) — семантика TD-0030.

    and наследует флаги; or ставит in_or и снимает skip (ветки не обязаны
    выполняться — подстрока может присутствовать через соседнюю ветку);
    `!` ставит skip = not in_or (под and отрицаемое гарантированно отсутствует,
    под or — может присутствовать). Прочие узлы обходятся консервативно.
    """
    op = next(iter(node))
    raw = node[op] if op in ("and", "or", "!") else list(node.values())
    items = raw if isinstance(raw, list) else [raw]
    if op == "or":
        return [(item, True, False) for item in items]
    if op == "!":
        return [(item, in_or, not in_or) for item in items]
    return [(item, in_or, skip) for item in items]


def _walk_substrings(node: Any, found: dict[str, None], in_or: bool = False, skip: bool = False) -> None:
    """Обход дерева фильтра для сбора message-подстрок (TD-0030)."""
    if isinstance(node, list):
        for item in node:
            _walk_substrings(item, found, in_or, skip)
        return
    if not isinstance(node, Mapping) or not node:
        return
    literal = _message_contains_literal(node)
    if literal is not None:
        if not skip:
            found.setdefault(literal, None)
        return
    for child, child_in_or, child_skip in _child_walk_plan(node, in_or, skip):
        _walk_substrings(child, found, child_in_or, child_skip)


def message_contains_substrings(tree: Any) -> tuple[str, ...]:
    """Строковые литералы contains по message из всего дерева фильтра, без повторов, в порядке появления (TD-0030).

    Литералы под `!` собираются только под `or`: там строка может содержать
    отрицаемую подстроку, пройдя фильтр по соседней ветке. Под `and` отрицаемое
    гарантированно отсутствует в строках выдачи — снипет невозможен, сбор
    опускается. Не-строковые и пустые литералы игнорируются.
    """
    found: dict[str, None] = {}
    _walk_substrings(tree, found)
    return tuple(found)
