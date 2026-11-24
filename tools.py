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
import mapping
import normalization
import pipeline
import semantic
import taxonomy
from schemas import (
    ConstraintsInput,
    FamilyDescription,
    GrokPattern,
    LayersInput,
    MappingFieldsInput,
    NormalizationFieldsInput,
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
    StagesInput,
    TransformOperation,
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
    constraints: ConstraintsInput = [],
) -> dict:
    """Проверяет residual, required, типы, диапазоны и межполевые ограничения; возвращает issues."""
    return grok.validate_result(parser_result, record_schema, constraints)


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
    constraints: ConstraintsInput = [],
) -> dict:
    """Независимо проверяет candidate и baseline со всеми ограничениями; ok=true только при успехе всех наблюдений."""
    return grok.evaluate_candidate(layers, patterns, record_schema, observations, baseline, constraints)


@tool
def probe_format(raw: RawInput) -> dict:
    """Детерминированно проверяет поддерживаемые форматы и возвращает кандидатов с доказательствами, без LLM-детекта."""
    return pipeline.probe_format(raw)


@tool
def try_parse(
    codec: Annotated[str, Field(description="grok — модельный конверт; cef/csv/kv — детерминированные payload-кодеки.")],
    raw: RawInput,
    params: Annotated[dict, Field(description="Для grok — {pattern, patterns}; для cef — {}; для csv/kv — разделители и правила подписей из probe.")] = {},
) -> dict:
    """Пробует кодек на полном входе и возвращает ok/fields/residual/error, не изменяя RAW.

    CSV: delimiter, quotechar, escapechar, doublequote, skipinitialspace, strict,
    label_separator, positional_prefix/names, field_names, strip_keys/values,
    allow_positionals, allow_empty_keys/fields, expected_columns, duplicate_policy.
    KV: pair_separator, key_value_separator, quoting как у CSV, positional_prefix/names,
    strip_keys/values, allow_positionals, allow_empty_keys/values, expected_pairs,
    duplicate_policy=error|first|last. Ошибки параметров возвращаются с recovery.
    """
    return pipeline.try_parse(codec, raw, params)


@tool
def execute_pipeline_parser(stages: StagesInput, raw: RawInput) -> dict:
    """Исполняет pipeline по явным source-связям и возвращает captures, trace и failed_layer."""
    return pipeline.execute_pipeline(stages, raw)


@tool
def validate_pipeline_result(
    parser_result: ParserResult,
    record_schema: RecordSchemaInput,
    constraints: ConstraintsInput = [],
) -> dict:
    """Проверяет pipeline residual, required, типы, диапазоны и межполевые ограничения; возвращает issues."""
    return grok.validate_result(parser_result, record_schema, constraints)


@tool
def evaluate_pipeline_candidate(
    stages: StagesInput,
    record_schema: RecordSchemaInput,
    observations: Annotated[
        list[Observation],
        Field(min_length=1, description="Непустой список RAW-строк для независимой проверки pipeline."),
    ],
    baseline: Annotated[
        ParserBaseline | None,
        Field(description="Прежний pipeline kind=pipeline и его observations; null для новой Family."),
    ] = None,
    constraints: ConstraintsInput = [],
) -> dict:
    """Независимо проверяет candidate и baseline pipeline; ok=true только при успехе всех наблюдений."""
    return pipeline.evaluate_candidate(stages, record_schema, observations, baseline, constraints)


@tool
def save_pipeline_family(
    family_id: Annotated[str, Field(min_length=1, description="Стабильный идентификатор грамматики pipeline.")],
    revision: Annotated[int, Field(ge=1, description="Положительный номер новой ревизии.")],
    stages: StagesInput,
    record_schema: RecordSchemaInput,
    evidence: Annotated[
        list[str],
        Field(min_length=1, description="Непустой список RAW, каждая строка обязана успешно пройти pipeline."),
    ],
    family_description: FamilyDescription,
    parser_description: ParserDescription,
    constraints: ConstraintsInput = [],
) -> dict:
    """Проверяет pipeline на всех evidence, сохраняет revision и индексирует Family/parser для поиска."""
    observations = [Observation(raw=raw) for raw in evidence]
    evaluation = pipeline.evaluate_observations(stages, record_schema, observations, constraints)
    if not evaluation["ok"]:
        return {
            "ok": False,
            "error": "pipeline_evidence_validation_failed",
            "message": "Family не сохранена: pipeline обязан успешно разобрать каждую evidence-строку.",
            "recovery": "Исправьте stages/record_schema/constraints или удалите нерелевантную evidence, затем повторите проверку.",
            "details": evaluation,
        }
    saved = storage.save_family(
        family_id,
        revision,
        [],
        [],
        [field.model_dump(mode="json") for field in record_schema],
        evidence,
        family_description.model_dump(mode="json"),
        parser_description.model_dump(mode="json"),
        [constraint.model_dump(mode="json") for constraint in constraints],
        [stage.model_dump(mode="json") for stage in stages],
    )
    if saved["ok"]:
        semantic.index_family(family_id, revision)
    return saved


@tool
def apply_normalization_operation(
    operation: TransformOperation,
    value: Annotated[str | None, Field(description="Одно исходное значение; null пропускается без изменений.")],
) -> dict:
    """Применяет одну normalization-операцию и возвращает ok/result/error без изменения исходного значения."""
    return normalization.apply_transform(operation, value)


@tool
def preview_normalization(
    family_id: Annotated[str, Field(min_length=1, description="Существующая parser Family.")],
    parser_revision: Annotated[int, Field(ge=1, description="Точная parser revision, из которой получить captures.")],
    fields: NormalizationFieldsInput,
    raw: RawInput,
) -> dict:
    """Сам исполняет сохранённый parser на RAW и затем normalization fields; возвращает captures/normalized/errors/trace."""
    return normalization.preview_normalization(family_id, parser_revision, fields, raw)


@tool
def evaluate_normalization(
    family_id: Annotated[str, Field(min_length=1, description="Существующая parser Family.")],
    parser_revision: Annotated[int, Field(ge=1, description="Точная parser revision.")],
    fields: NormalizationFieldsInput,
    evidence: Annotated[list[str], Field(min_length=1, description="Непустой набор RAW для независимой проверки.")],
) -> dict:
    """Проверяет parser и normalization на всех evidence, включая полноту описаний record_schema."""
    return normalization.evaluate_normalization(family_id, parser_revision, fields, evidence)


@tool
def save_normalization(
    family_id: Annotated[str, Field(min_length=1, description="Существующая parser Family.")],
    revision: Annotated[int, Field(ge=1, description="Независимый номер normalization revision.")],
    parser_revision: Annotated[int, Field(ge=1, description="Точная parser revision, к которой относятся правила.")],
    fields: NormalizationFieldsInput,
    evidence: Annotated[list[str], Field(min_length=1, description="RAW evidence; каждая строка обязана пройти parser и normalization.")],
    description: Annotated[str, Field(min_length=1, description="Допущения, правила и ограничения normalization revision.")],
) -> dict:
    """Сохраняет normalization revision только после успешной проверки всех evidence."""
    evaluation = normalization.evaluate_normalization(family_id, parser_revision, fields, evidence)
    if not evaluation["ok"]:
        return {
            "ok": False,
            "error": "normalization_validation_failed",
            "message": "Normalization revision не сохранена: исправьте описания, operations или evidence.",
            "recovery": "Используйте errors/issues результата, повторите preview/evaluate и только затем сохраните.",
            "details": evaluation,
        }
    return storage.save_normalization(
        family_id,
        revision,
        parser_revision,
        [field.model_dump(mode="json") for field in fields],
        evidence,
        description,
    )


@tool
def get_normalization(
    family_id: Annotated[str, Field(min_length=1, description="Точная parser Family.")],
    revision: Annotated[int | None, Field(ge=1, description="Normalization revision; null — последняя подходящая.")] = None,
    parser_revision: Annotated[int | None, Field(ge=1, description="Если задано, выбрать правила именно для этой parser revision.")] = None,
) -> dict:
    """Возвращает сохранённую normalization revision с описаниями, operations и evidence."""
    return storage.get_normalization(family_id, revision, parser_revision)


@tool
def get_mapping_guidelines() -> dict:
    """Возвращает полный порядок маппинга MP SIEM из mp_siem_8_mapping_sequence.md."""
    return taxonomy.mapping_guidelines()


@tool
def semantic_search_taxonomy(
    kind: Annotated[str, Field(description="taxonomy_field, taxonomy_value или taxonomy_category.")],
    query: Annotated[str, Field(min_length=1, description="Смысл исходного поля и нужного кандидата MP SIEM.")],
    field: Annotated[str | None, Field(description="Для taxonomy_value ограничить поиск Enum-полем, например action.")] = None,
) -> dict:
    """Ищет поля, Enum values или категории MP SIEM по их описаниям через embeddings."""
    return taxonomy.search_taxonomy(kind, query, field)


@tool
def get_taxonomy_field(
    name: Annotated[str, Field(min_length=1, description="Точное плоское имя поля MP SIEM, например src.ip.")],
) -> dict:
    """Возвращает тип, обязательность, automatic flag и описание поля MP SIEM."""
    return storage.get_taxonomy_field(taxonomy.TAXONOMY, name)


@tool
def get_taxonomy_values(
    field: Annotated[str | None, Field(description="Enum-поле для фильтра; null — все значения таксономии.")] = None,
) -> dict:
    """Возвращает допустимые Enum values и их описания из MP SIEM reference."""
    return storage.get_taxonomy_values(taxonomy.TAXONOMY, field)


@tool
def get_taxonomy_categories() -> dict:
    """Возвращает официальную иерархию category.generic/high/low."""
    return storage.get_taxonomy_categories(taxonomy.TAXONOMY)


@tool
def preview_mapping(
    family_id: Annotated[str, Field(min_length=1, description="Существующая parser Family.")],
    parser_revision: Annotated[int, Field(ge=1, description="Точная parser revision.")],
    normalization_revision: Annotated[int, Field(ge=1, description="Точная normalization revision.")],
    mappings: MappingFieldsInput,
    raw: RawInput,
    source_timezone: Annotated[str | None, Field(description="Внешняя timezone для naive timestamp; null, если не задана.")] = None,
) -> dict:
    """Исполняет parser и normalization на RAW, затем модельный mapping и проверяет событие MP SIEM."""
    return mapping.preview_mapping(
        family_id, parser_revision, normalization_revision, mappings, raw, source_timezone
    )


@tool
def evaluate_mapping(
    family_id: Annotated[str, Field(min_length=1, description="Существующая parser Family.")],
    parser_revision: Annotated[int, Field(ge=1, description="Точная parser revision.")],
    normalization_revision: Annotated[int, Field(ge=1, description="Точная normalization revision.")],
    mappings: MappingFieldsInput,
    evidence: Annotated[list[str], Field(min_length=1, description="Непустой набор RAW evidence.")],
    source_timezone: Annotated[str | None, Field(description="Внешняя timezone для naive timestamp; null, если не задана.")] = None,
) -> dict:
    """Независимо проверяет модельный mapping и схему MP SIEM на всех evidence."""
    return mapping.evaluate_mappings(
        family_id, parser_revision, normalization_revision, mappings, evidence, source_timezone
    )


@tool
def save_mapping(
    family_id: Annotated[str, Field(min_length=1, description="Существующая parser Family.")],
    revision: Annotated[int, Field(ge=1, description="Независимый номер mapping revision.")],
    parser_revision: Annotated[int, Field(ge=1, description="Точная parser revision.")],
    normalization_revision: Annotated[int, Field(ge=1, description="Точная normalization revision.")],
    mappings: MappingFieldsInput,
    evidence: Annotated[list[str], Field(min_length=1, description="RAW evidence для полной проверки.")],
    description: Annotated[str, Field(min_length=1, description="Решения, допущения и ограничения mapping revision.")],
    source_timezone: Annotated[str | None, Field(description="Внешняя timezone, заданная runner; не выводить из RAW.")] = None,
) -> dict:
    """Сохраняет mapping revision только после успешной проверки parser, normalization и MP SIEM schema."""
    evaluation = mapping.evaluate_mappings(
        family_id, parser_revision, normalization_revision, mappings, evidence, source_timezone
    )
    if not evaluation["ok"]:
        return {
            "ok": False,
            "error": "mapping_validation_failed",
            "message": "Mapping revision не сохранена: исправьте соответствия по errors и справочнику.",
            "recovery": "Используйте semantic_search_taxonomy/get_taxonomy_field, повторите evaluate и затем сохраните.",
            "details": evaluation,
        }
    return storage.save_mapping(
        family_id,
        revision,
        parser_revision,
        normalization_revision,
        taxonomy.TAXONOMY,
        source_timezone,
        [item.model_dump(mode="json") for item in mappings],
        evidence,
        description,
    )


@tool
def get_mapping(
    family_id: Annotated[str, Field(min_length=1, description="Точная parser Family.")],
    revision: Annotated[int | None, Field(ge=1, description="Mapping revision; null — последняя подходящая.")] = None,
    normalization_revision: Annotated[int | None, Field(ge=1, description="Фильтр по normalization revision.")] = None,
) -> dict:
    """Возвращает сохранённый модельный mapping в MP SIEM с evidence и обоснованиями."""
    return storage.get_mapping(family_id, revision, normalization_revision)


@tool
def save_grok_pattern(
    name: PatternName,
    definition: PatternDefinition,
    description: Annotated[str, Field(min_length=1, description="Назначение и границы применения конструкции.")],
    positive_examples: Annotated[
        list[str],
        Field(min_length=1, description="Непустой список полных входов, которые конструкция обязана принять."),
    ],
    negative_examples: Annotated[
        list[str],
        Field(min_length=1, description="Непустой список полных входов, которые конструкция обязана отклонить."),
    ],
) -> dict:
    """Сохраняет Grok-конструкцию и её embedding для семантического поиска через настроенного провайдера."""
    saved = storage.save_grok(name, definition, description, positive_examples, negative_examples)
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
    """Ищет последние parser revisions по смыслу через embedding API; история не участвует в обычном поиске."""
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
    constraints: ConstraintsInput = [],
) -> dict:
    """Проверяет примеры, сохраняет ревизию и индексирует Family и parser для семантического поиска."""
    pattern_issues = grok.validate_patterns(patterns)
    if pattern_issues:
        return {
            "ok": False,
            "error": "grok_pattern_validation_failed",
            "message": "Family не сохранена: исправьте зависимости и исполняемые примеры.",
            "issues": pattern_issues,
        }
    for pattern in patterns:
        existing = storage.get_grok(pattern.name)
        if not existing["ok"]:
            continue
        stored = existing["pattern"]
        if stored["definition"] != pattern.definition:
            return {
                "ok": False,
                "error": "grok_registry_conflict",
                "message": "Определение Family расходится с глобальным Grok. Используйте сохранённое определение или новое имя.",
                "pattern": pattern.name,
                "stored_definition": stored["definition"],
                "received_definition": pattern.definition,
            }
    saved = storage.save_family(
        family_id,
        revision,
        [pattern.model_dump(mode="json") for pattern in patterns],
        [layer.model_dump(mode="json") for layer in layers],
        [field.model_dump(mode="json") for field in record_schema],
        evidence,
        family_description.model_dump(mode="json"),
        parser_description.model_dump(mode="json"),
        [constraint.model_dump(mode="json") for constraint in constraints],
    )
    if saved["ok"]:
        semantic.index_family(family_id, revision)
    return saved


@tool
def get_parser_research(
    family_id: Annotated[str, Field(min_length=1, description="Точная parser Family.")],
    revision: Annotated[int, Field(ge=1, description="Точная parser revision.")],
) -> dict:
    """Возвращает исходный полный Response парсинг-агента: essence, structure, strategy, features, pitfalls и parser."""
    return storage.get_parser_research(family_id, revision)


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
    probe_format,
    try_parse,
    execute_pipeline_parser,
    validate_pipeline_result,
    evaluate_pipeline_candidate,
    save_pipeline_family,
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


NORMALIZATION_TOOLS = [
    duckduckgo_search,
    apply_normalization_operation,
    preview_normalization,
    evaluate_normalization,
    save_normalization,
    get_normalization,
    get_parser_family,
    get_parser_research,
]


MAPPING_TOOLS = [
    duckduckgo_search,
    get_mapping_guidelines,
    semantic_search_taxonomy,
    get_taxonomy_field,
    get_taxonomy_values,
    get_taxonomy_categories,
    preview_mapping,
    evaluate_mapping,
    save_mapping,
    get_mapping,
    get_normalization,
    get_parser_family,
    get_parser_research,
]
