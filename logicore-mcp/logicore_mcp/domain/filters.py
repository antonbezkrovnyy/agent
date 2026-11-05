"""Схема полей MARS и подсказки похожих имён для аргументов инструментов."""

from collections.abc import Mapping

from pydantic import BaseModel


class FieldSchema(BaseModel):
    """Имя поля и его ClickHouse-тип из живой схемы MARS."""

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
        """Класс значения для проверки аргументов: string | number | bool | datetime | array | other."""
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
    """Дамерау-Левенштейн с ранним выходом: расстояние <= ceiling."""
    if abs(len(a) - len(b)) > ceiling:
        return False
    max_len = max(len(a), len(b)) + 1
    da: dict[str, int] = {}
    d = [[max_len] * (len(b) + 1) for _ in range(len(a) + 1)]
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
    """Похожие имена для ошибки «неизвестное поле»: префикс, подстрока, опечатка."""
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
        and abs(len(f) - len(name)) <= 2
        and _edit_distance_within(f.lower(), lowered, 2)
    ]
    return (prefix + contains + inside_query + typo)[:limit]
