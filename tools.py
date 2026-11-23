from collections.abc import Callable
import json
from datetime import datetime
from typing import Annotated

from ddgs import DDGS
from langchain.agents.middleware import wrap_tool_call
from langchain.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.tools import tool
from pydantic import Field, ValidationError

import grok
import semantic
from schemas import (
    FamilyDescription,
    GrokPattern,
    LayersInput,
    Observation,
    ParserBaseline,
    ParserDescription,
    ParserResult,
    PatternDefinition,
    PatternMap,
    PatternName,
    RawInput,
    RecordField,
    RecordSchemaInput,
)
import storage


def error_response(code: str, message: str, **details) -> str:
    return json.dumps(
        {"ok": False, "error": code, "message": message, **details},
        ensure_ascii=False,
        default=str,
    )


def validation_error_response(
    error: ValidationError,
    recovery: str = "Исправьте перечисленные параметры и повторите вызов.",
) -> str:
    details = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "arguments"
        details.append({
            "parameter": location,
            "problem": item["msg"],
            "received": repr(item.get("input"))[:500],
        })
    return error_response(
        "invalid_arguments",
        recovery,
        invalid_parameters=details,
        expected="JSON-объект с типами и вложенными полями из требуемой схемы данных.",
    )


def tool_error_response(error: Exception) -> str:
    return error_response(
        "tool_execution_failed",
        f"Инструмент не выполнен: {type(error).__name__}: {error}",
        recovery="Исправьте указанную причину или используйте другой корректный вызов.",
    )


@wrap_tool_call
def handle_tool_errors(
    request: ToolCallRequest,
    handler: Callable[[ToolCallRequest], ToolMessage],
) -> ToolMessage:
    try:
        return handler(request)
    except ValidationError as error:
        content = validation_error_response(error)
    except Exception as error:
        content = tool_error_response(error)
    return ToolMessage(
        content=content,
        tool_call_id=request.tool_call["id"],
        name=request.tool_call["name"],
        status="error",
    )


@tool
def current_time() -> str:
    """Возвращает текущие локальные дату и время в ISO 8601; входных параметров нет."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@tool
def duckduckgo_search(
    query: Annotated[str, Field(min_length=1, description="Конкретный поисковый запрос для разрешения неоднозначности.")],
) -> dict:
    """Ищет документацию в DuckDuckGo и возвращает до пяти результатов title/url/snippet."""
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=5)
    except Exception as error:
        return {
            "ok": False,
            "error": "search_failed",
            "message": f"{type(error).__name__}: {error}",
            "recovery": "Продолжите анализ по наблюдениям или повторите поиск с другим запросом.",
            "results": [],
        }
    return {
        "ok": True,
        "results": [
            {
                "title": str(result.get("title", "")),
                "url": str(result.get("href", "")),
                "snippet": str(result.get("body", "")),
            }
            for result in results
        ],
    }


@tool
def execute_grok(
    pattern: Annotated[str, Field(min_length=1, description="Имя конструкции или полное Grok-выражение для fullmatch.")],
    input: RawInput,
    patterns: PatternMap = {},
) -> dict:
    """Исполняет одну Grok-конструкцию на всём input и возвращает ok/captures/matched/residual/error."""
    return grok.execute_pattern(pattern, input, patterns)


@tool
def execute_grok_parser(
    layers: LayersInput,
    patterns: PatternMap,
    raw: RawInput,
) -> dict:
    """Последовательно исполняет все слои Grok-парсера и возвращает captures, trace и failed_layer."""
    return grok.execute_parser(layers, patterns, raw)


@tool
def validate_grok_result(
    parser_result: ParserResult,
    record_schema: RecordSchemaInput,
) -> dict:
    """Проверяет успех, пустой residual, обязательные captures и типы integer/ipv4; возвращает список issues."""
    return grok.validate_result(parser_result, record_schema)


@tool
def evaluate_candidate(
    layers: LayersInput,
    patterns: PatternMap,
    record_schema: RecordSchemaInput,
    observations: Annotated[
        list[Observation],
        Field(min_length=1, description="Непустой список строк для независимой проверки кандидата."),
    ],
    baseline: Annotated[
        ParserBaseline | None,
        Field(description="Прежний парсер и его наблюдения; null, если независимый baseline не требуется."),
    ] = None,
) -> dict:
    """Независимо проверяет candidate и optional baseline; общий ok=true только при успехе всех наблюдений."""
    return grok.evaluate_candidate(layers, patterns, record_schema, observations, baseline)


@tool
def save_grok_pattern(
    name: PatternName,
    definition: PatternDefinition,
    description: Annotated[str, Field(min_length=1, description="Назначение и границы применения конструкции.")],
) -> dict:
    """Сохраняет Grok-конструкцию и её embedding для семантического поиска через настроенного провайдера."""
    saved = storage.save_grok(name, definition, description)
    if saved["ok"]:
        semantic.index_grok(name)
    return saved


@tool
def get_grok_pattern(name: PatternName) -> dict:
    """Возвращает одну сохранённую Grok-конструкцию по точному имени или ошибку not_found."""
    return storage.get_grok(name)


@tool
def search_grok_patterns(
    query: Annotated[str, Field(min_length=1, description="Подстрока для поиска по имени, определению и описанию.")],
) -> dict:
    """Ищет сохранённые Grok-конструкции и возвращает список name/definition/description."""
    return storage.search_grok(query)


@tool
def semantic_search_grok_patterns(
    query: Annotated[str, Field(min_length=1, description="Назначение нужного атома, ожидаемые значения и границы; не обязательно точное имя.")],
) -> dict:
    """Ищет Grok по смыслу через настроенный embedding API; возвращает все кандидаты с cosine score."""
    return semantic.search_grok_semantic(query)


@tool
def semantic_search_parser_families(
    query: Annotated[str, Field(min_length=1, description="Смысл события, конверт, payload и наблюдённые идентификаторы искомой Family.")],
) -> dict:
    """Ищет последние ревизии Family по смыслу; score только ранжирует кандидатов и не заменяет исполнение."""
    return semantic.search_families_semantic(query)


@tool
def semantic_search_parsers(
    query: Annotated[str, Field(min_length=1, description="Входные данные, слои, выходные поля и особенности нужной parser revision.")],
) -> dict:
    """Ищет все parser revisions по смыслу через embedding API; возвращает family_id/revision/score."""
    return semantic.search_parsers_semantic(query)


@tool
def save_parser_family(
    family_id: Annotated[str, Field(min_length=1, description="Стабильный идентификатор грамматики, например syslog_acl_verdict.")],
    revision: Annotated[int, Field(ge=1, description="Положительный номер ревизии; для другого содержимого используйте новый номер.")],
    patterns: Annotated[
        list[GrokPattern],
        Field(description="Список объектов name/definition/description, а не объект ИМЯ → regex."),
    ],
    layers: LayersInput,
    record_schema: RecordSchemaInput,
    evidence: Annotated[
        list[str],
        Field(min_length=1, description="Непустой список исходных RAW-строк, подтверждающих эту ревизию."),
    ],
    family_description: Annotated[
        FamilyDescription,
        Field(description="Развёрнутое описание стабильной Family и её положительных/отрицательных границ."),
    ],
    parser_description: Annotated[
        ParserDescription,
        Field(description="Развёрнутое описание конкретной parser revision, регрессии и ограничений."),
    ],
) -> dict:
    """Сохраняет ревизию и два описания, затем индексирует Family и parser для семантического поиска."""
    saved = storage.save_family(
        family_id,
        revision,
        [pattern.model_dump(mode="json") for pattern in patterns],
        [layer.model_dump(mode="json") for layer in layers],
        [field.model_dump(mode="json") for field in record_schema],
        evidence,
        family_description.model_dump(mode="json"),
        parser_description.model_dump(mode="json"),
    )
    if saved["ok"]:
        semantic.index_family(family_id, revision)
    return saved


@tool
def list_parser_families() -> dict:
    """Перечисляет последние ревизии всех Family с family_id/revision/evidence; входных параметров нет."""
    return storage.list_families()


@tool
def get_parser_family(
    family_id: Annotated[str, Field(min_length=1, description="Точный идентификатор сохранённой Family.")],
    revision: Annotated[
        int | None,
        Field(ge=1, description="Номер ревизии; null или отсутствие параметра — последняя ревизия."),
    ] = None,
) -> dict:
    """Возвращает выбранную или последнюю ревизию Family со всеми данными и описаниями."""
    return storage.get_family(family_id, revision)


TOOLS = [
    current_time,
    duckduckgo_search,
    execute_grok,
    execute_grok_parser,
    validate_grok_result,
    evaluate_candidate,
    save_grok_pattern,
    get_grok_pattern,
    search_grok_patterns,
    semantic_search_grok_patterns,
    semantic_search_parser_families,
    semantic_search_parsers,
    save_parser_family,
    list_parser_families,
    get_parser_family,
]
