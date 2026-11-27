from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchemaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StructureLayer(SchemaModel):
    """Один обоснованный слой пути от исходной строки к извлекаемому значению."""

    path: str = Field(description="Уникальный путь к слою или значению, например $.header.timestamp.")
    format: str = Field(description="Наблюдаемый или предполагаемый формат содержимого этого пути.")
    role: Literal["envelope", "payload", "value"] = Field(
        description="Роль: envelope — конверт, payload — вложенные данные, value — конечное значение."
    )
    transform: str | None = Field(
        description="Преобразование, необходимое для перехода к содержимому слоя; null, если его нет."
    )
    confidence: Literal["observed", "inferred", "hypothesis"] = Field(
        description="Степень подтверждения: наблюдение, вывод из наблюдений или проверяемая гипотеза."
    )


class RecordField(SchemaModel):
    """Поле минимального контракта результата детерминированного разбора одной Family."""

    name: str = Field(description="Имя выходного поля; должно совпадать с именем Grok capture или поля pipeline, если извлекается.")
    category: Literal["static", "dynamic"] = Field(
        description="static — стабильная грамматика Family; dynamic — значение конкретного наблюдения."
    )
    type: str = Field(description="Логический тип значения, например string, integer, ipv4 или timestamp.")
    required: bool = Field(
        description="True только если корректный разбор любой допустимой записи Family невозможен без этого поля."
    )
    source: str = Field(
        description="Путь из structure к исходному значению либо явное обозначение контракта Family для static-поля."
    )


class FormatFeature(SchemaModel):
    """Свойство формата и непосредственное следствие этого свойства для разбора."""

    property: str = Field(description="Конкретное свойство формата с пометкой observed, inferred или hypothesis.")
    implication: str = Field(description="Решение или ограничение разбора, которое следует из этого свойства.")


class Pitfall(SchemaModel):
    """Неоднозначность самого формата, механизм ошибки и способ её разрешения."""

    name: str = Field(description="Короткое однозначное название особенности, приводящей к ошибке.")
    mechanism: str = Field(description="Как именно это свойство формата приводит к неверному или неполному разбору.")
    remediation: str = Field(description="Правило разбора или нормализации, устраняющее неоднозначность.")


class GrokPattern(SchemaModel):
    """Именованная человекочитаемая Grok-конструкция без семантического capture по умолчанию."""

    name: str = Field(description="Уникальное имя конструкции из букв, цифр и подчёркиваний, например SYSLOGTIMEWITHYEAR.")
    definition: str = Field(
        description="Regex-тело конструкции; может ссылаться на другие конструкции через %{ИМЯ} или %{ИМЯ:capture}."
    )
    description: str = Field(description="Какой фрагмент формата распознаёт конструкция и каковы границы её применения.")
    positive_examples: list[str] = Field(
        default_factory=list,
        description="Примеры, которые конструкция обязана принять; минимум один для новой конструкции.",
    )
    negative_examples: list[str] = Field(
        default_factory=list,
        description="Примеры, которые конструкция обязана отклонить; минимум один для новой конструкции.",
    )


class GrokLayer(SchemaModel):
    """Один последовательно исполняемый слой Grok-парсера."""

    name: str = Field(description="Уникальное имя слоя для диагностики failed_layer.")
    source: str = Field(
        description="Для первого слоя — raw; для следующего — имя capture, полученного непосредственно предыдущим слоем."
    )
    pattern: str = Field(
        description="Grok-выражение для полного совпадения входа слоя; используй %{ИМЯ:capture} для извлечения полей."
    )


class FieldConstraint(SchemaModel):
    """Ограничение значения или присутствия capture в зависимости от других captures."""

    field: str = Field(description="Имя capture или pipeline-поля, к которому применяется ограничение.")
    equals: str | int | float | bool | None = Field(
        default=None,
        description="Если задано, поле обязано точно равняться значению; используется как детерминированный ключ dispatch.",
    )
    allowed_when: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Если field присутствует, перечисленные captures должны иметь одно из допустимых значений.",
    )
    required_when: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Если перечисленные captures имеют одно из значений, field обязан быть непустым.",
    )
    description: str = Field(description="Смысловое обоснование ограничения и наблюдения, на которых оно основано.")


class PipelineStage(SchemaModel):
    """Одна детерминированная стадия разбора с явным источником входа."""

    name: str = Field(description="Уникальное имя стадии для trace и диагностики failed_layer.")
    codec: Literal["grok", "cef", "csv", "kv"] = Field(description="Модельный Grok либо детерминированный кодек payload.")
    source: str = Field(
        description=(
            "Для первой стадии — raw; для следующей — stages.<имя_предыдущей>.fields.<поле>, "
            "например stages.envelope.fields.message."
        )
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Для grok — pattern/patterns; для cef — {}; для csv/kv — параметры разделителей и подписей.",
    )


class ParserDefinition(SchemaModel):
    """Grok-слои, детерминированный pipeline либо native MP TEXT и ограничения одной revision."""

    kind: Literal["grok", "pipeline", "mp_text"] = Field(
        default="grok",
        description="grok — patterns/layers; pipeline — stages; mp_text — native MP TEXT text_pattern. Режимы нельзя смешивать.",
    )
    patterns: list[GrokPattern] = Field(
        default_factory=list,
        description="Пользовательские Grok-конструкции; только для kind=grok.",
    )
    layers: list[GrokLayer] = Field(
        default_factory=list,
        description="Слои Grok-разбора от raw к вложенным данным; только для kind=grok.",
    )
    stages: list[PipelineStage] = Field(
        default_factory=list,
        description="Упорядоченные детерминированные стадии; обязательны и только для kind=pipeline.",
    )
    text_pattern: str | None = Field(
        default=None,
        description="Полный native MP TEXT pattern с temporary $captures; обязателен и только для kind=mp_text.",
    )
    negative_evidence: list[str] = Field(
        default_factory=list,
        description="RAW, которые MP TEXT pattern обязан отклонить; сохраняются вместе с Family.",
    )
    constraints: list[FieldConstraint] = Field(
        default_factory=list,
        description="Межполевые ограничения, которые невозможно выразить одной конструкцией без ложных комбинаций.",
    )

    @model_validator(mode="after")
    def check_parser_mode(self):
        if self.kind == "grok":
            if self.stages or self.text_pattern is not None:
                raise ValueError("Для kind=grok stages/text_pattern должны быть пустыми.")
            if not self.layers:
                raise ValueError("Для kind=grok layers не должен быть пустым.")
        elif self.kind == "pipeline":
            if not self.stages:
                raise ValueError("Для kind=pipeline stages не должен быть пустым.")
            if self.patterns or self.layers or self.text_pattern is not None:
                raise ValueError("Для kind=pipeline patterns/layers/text_pattern должны быть пустыми.")
        else:
            if not self.text_pattern:
                raise ValueError("Для kind=mp_text text_pattern обязателен.")
            if self.patterns or self.layers or self.stages:
                raise ValueError("Для kind=mp_text patterns/layers/stages должны быть пустыми.")
        return self


class Response(SchemaModel):
    """Полный результат исследования одной parser Family: смысл, контракт и исполняемый parser."""

    essence: str = Field(description="Кратко: участники события, действие и результат без рекомендаций и догадок.")
    structure: list[StructureLayer] = Field(description="Упорядоченные слои от RAW к значениям без выдуманной вложенности.")
    strategy: list[str] = Field(description="Только проектные решения; без альтернатив, обоснований и рекомендаций.")
    record_schema: list[RecordField] = Field(description="Минимально полный контракт выходных полей одной Family.")
    format_features: list[FormatFeature] = Field(description="Свойства формата и обязательные следствия для разбора.")
    pitfalls: list[Pitfall] = Field(description="Только неоднозначности формата, их механизм и разрешение.")
    parser: ParserDefinition = Field(description="Исполненный Grok-парсер или детерминированный pipeline на исходной строке.")


class LogSample(SchemaModel):
    sample_id: str = Field(min_length=1, description="Стабильный идентификатор примера внутри батча.")
    raw: str = Field(description="Полная исходная RAW-строка без изменений.")


class ResearchBatch(SchemaModel):
    cluster_id: int = Field(description="Идентификатор технического кластера предварительной группировки.")
    total_logs_in_cluster: int = Field(ge=1, description="Полное число строк в исходном кластере.")
    samples: list[LogSample] = Field(min_length=1, description="Контрастные RAW-примеры для исследования.")

    @model_validator(mode="after")
    def check_batch(self):
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(set(sample_ids)) != len(sample_ids):
            raise ValueError("sample_id должен быть уникальным внутри ResearchBatch.")
        if self.total_logs_in_cluster < len(self.samples):
            raise ValueError("total_logs_in_cluster не может быть меньше числа samples.")
        return self


class FamilyResearchGroup(SchemaModel):
    sample_ids: list[str] = Field(min_length=1, description="Примеры, описываемые одной parser Family.")
    response: Response = Field(description="Обычный результат исследования одной parser Family.")

    @model_validator(mode="after")
    def check_unique_samples(self):
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("sample_id не должен повторяться внутри FamilyResearchGroup.")
        return self


class ResearchBatchResponse(SchemaModel):
    groups: list[FamilyResearchGroup] = Field(
        default_factory=list,
        description="Одна или несколько независимых parser groups; каждая содержит обычный Response.",
    )
    unresolved_sample_ids: list[str] = Field(
        default_factory=list,
        description="Примеры, для которых недостаточно доказательств надёжного parser.",
    )

    @model_validator(mode="after")
    def check_unresolved_samples(self):
        if len(set(self.unresolved_sample_ids)) != len(self.unresolved_sample_ids):
            raise ValueError("sample_id не должен повторяться в unresolved_sample_ids.")
        return self


class ParserResult(SchemaModel):
    """Результат Grok- или pipeline-разбора, проверяемый на полноту и обязательные поля."""

    ok: bool = Field(description="True только при полном разборе всех слоёв с пустым residual.")
    captures: dict[str, str] = Field(description="Извлечённые строковые значения по именам captures.")
    residual: str = Field(description="Неразобранный остаток; при успехе должен быть пустой строкой.")
    error: str | None = Field(default=None, description="Код ошибки исполнения либо null при успехе.")
    trace: list[dict[str, Any]] = Field(default_factory=list, description="Трассировка отдельных слоёв, если она возвращена.")
    failed_layer: str | None = Field(default=None, description="Имя слоя, на котором разбор остановился.")


class Observation(SchemaModel):
    """Одна исходная строка и опциональные эталонные captures для регрессионной проверки."""

    raw: str = Field(description="Исходная строка целиком, без предварительного изменения или unescaping.")
    expected: dict[str, str | int | float | bool] = Field(
        default_factory=dict,
        description="Ожидаемые capture: значение; сравниваются со строковым представлением фактического capture.",
    )


class ParserBaseline(SchemaModel):
    """Прежняя Grok- или pipeline-ревизия и её наблюдения для независимой регрессии."""

    kind: Literal["grok", "pipeline", "mp_text"] = Field(default="grok", description="Тип прежнего parser; должен совпадать с candidate.")
    text_pattern: str | None = Field(default=None, description="Прежний native MP TEXT pattern для kind=mp_text.")
    layers: list[GrokLayer] = Field(default_factory=list, description="Слои прежнего Grok-парсера без изменений.")
    patterns: dict[str, str] = Field(
        default_factory=dict,
        description="Отображение ИМЯ → regex-тело для всех конструкций прежнего Grok-парсера.",
    )
    stages: list[PipelineStage] = Field(default_factory=list, description="Стадии прежнего pipeline без изменений.")
    record_schema: list[RecordField] = Field(
        default_factory=list,
        description="Схема прежнего парсера; пустой список означает использование схемы кандидата.",
    )
    observations: list[Observation] = Field(description="Строки, на которых прежний парсер обязан сохранить результат.")
    negative_evidence: list[str] = Field(default_factory=list, description="Negative RAW прежнего MP TEXT parser.")
    constraints: list[FieldConstraint] = Field(
        default_factory=list,
        description="Межполевые ограничения прежнего parser; пустой список означает отсутствие дополнительных правил.",
    )


class FamilyDescription(SchemaModel):
    """Семантическое описание стабильной Family, общее для её parser revisions."""

    summary: str = Field(description="Кратко: назначение Family и тип разбираемых событий.")
    event_semantics: str = Field(description="Участники, действие и смысл событий без привязки к одному RAW-примеру.")
    envelope_and_payload: str = Field(description="Наблюдённые конверты, вложенность, преобразования и варианты payload.")
    dispatch: str = Field(description="Стабильный ключ диспетчеризации, его значение и источник; если ключа нет — явно указать.")
    applicability: str = Field(description="Положительные границы: какие грамматические варианты принадлежат Family.")
    exclusions: str = Field(description="Отрицательные границы и похожие форматы, которые Family не должна разбирать.")


class ParserDescription(SchemaModel):
    """Техническое и смысловое описание одной parser revision для семантического поиска."""

    summary: str = Field(description="Кратко: что разбирает эта ревизия и чем она отличается от других.")
    input_contract: str = Field(description="Ожидаемый RAW, конверт, кодировка и необходимые преобразования.")
    layering: str = Field(description="Порядок Grok-слоёв или pipeline-стадий, связи источников и использованные конструкции/кодеки.")
    output_contract: str = Field(description="Основные выходные поля, типы, обязательность и политика residual.")
    revision_reason: str = Field(description="Причина создания новой ревизии; для первой — основание создания parser.")
    regression_and_limitations: str = Field(
        description="Evidence, регрессионный результат, отрицательные примеры и известные ограничения."
    )


class TransformOperation(SchemaModel):
    """Одна детерминированная операция над значением без переименования поля."""

    op: Literal["trim", "null_if", "to_integer", "lowercase", "normalize_ip", "validate_range", "parse_timestamp"] = Field(
        description="Имя поддерживаемой операции; параметры зависят от операции."
    )
    params: dict[str, Any] = Field(default_factory=dict, description="Параметры операции; неизвестные параметры отклоняются.")


class NormalizationField(SchemaModel):
    """Описание и последовательные преобразования одного исходного capture."""

    source: str = Field(description="Точное имя capture, обычно совпадает с полем record_schema; имя результата сохраняется.")
    description: str = Field(
        min_length=1,
        description="Развёрнутый смысл поля, единицы, направление, null/zero, допустимые значения и неопределённости для будущего маппинга.",
    )
    operations: list[TransformOperation] = Field(
        default_factory=list,
        description="Упорядоченные операции над значением; пустой список сохраняет исходное значение.",
    )
    required: bool = Field(description="True, если отсутствие или null после преобразований является ошибкой.")


class NormalizationRevision(SchemaModel):
    """Проверенная нормализация полей конкретной parser revision."""

    family_id: str = Field(description="Существующая parser Family.")
    revision: int = Field(ge=1, description="Независимый номер normalization revision.")
    parser_revision: int = Field(ge=1, description="Точная parser revision, к которой применяются правила.")
    fields: list[NormalizationField] = Field(
        description="Описания всех выходных полей Family, включая поля без преобразований."
    )
    evidence: list[str] = Field(description="RAW-строки, на которых parser и normalization обязаны успешно исполниться.")
    description: str = Field(description="Общее описание решений, допущений и ограничений этой normalization revision.")


class MappingField(SchemaModel):
    """Одно выбранное моделью соответствие normalized source и поля MP SIEM."""

    source: str | None = Field(
        default=None,
        description="Имя normalized capture; null только для константы, выбранной по смыслу Family.",
    )
    target: str = Field(description="Точное плоское имя поля MP SIEM, например src.ip или category.low.")
    constant: Any | None = Field(
        default=None,
        description="Константа, выбранная моделью по документации; нельзя использовать для скрытия неизвестного source.",
    )
    value_map: dict[str, Any] | None = Field(
        default=None,
        description="Явное соответствие исходных значений целевым; неизвестные значения дают ошибку, а не default.",
    )
    condition: dict[str, Any] | None = Field(
        default=None,
        description="Опциональное условие применения: {format: ip|ipv4|ipv6|hostname}; применимо только к source.",
    )
    description: str = Field(description="Семантическое обоснование соответствия по описаниям source и таксономии.")

    @model_validator(mode="after")
    def check_mapping_source(self):
        if self.source is None and self.constant is None:
            raise ValueError("MappingField требует source или non-null constant.")
        if self.source is not None and self.constant is not None:
            raise ValueError("MappingField не может одновременно содержать source и constant.")
        if self.value_map is not None and self.source is None:
            raise ValueError("value_map требует source.")
        if self.condition is not None and self.source is None:
            raise ValueError("condition требует source.")
        return self


class MappingRevision(SchemaModel):
    """Проверенный маппинг normalization revision в MP SIEM 8.0."""

    family_id: str = Field(description="Существующая parser Family.")
    revision: int = Field(ge=1, description="Независимый номер mapping revision.")
    parser_revision: int = Field(ge=1, description="Точная parser revision.")
    normalization_revision: int = Field(ge=1, description="Точная normalization revision, чьи значения маппятся.")
    taxonomy: str = Field(description="Идентификатор таксономии, например mp_siem_8.0.")
    source_timezone: str | None = Field(
        default=None,
        description="Внешняя настройка timezone для naive timestamp; не выводится моделью из RAW.",
    )
    mappings: list[MappingField] = Field(description="Выбранные моделью соответствия и константы события MP SIEM.")
    evidence: list[str] = Field(description="RAW-строки, на которых parser, normalization и mapping проверены.")
    description: str = Field(description="Решения, допущения и ограничения mapping revision.")


MappingFieldsInput = Annotated[
    list[MappingField],
    Field(description="Модельные соответствия normalized source → MP SIEM target или обоснованные константы."),
]


NormalizationFieldsInput = Annotated[
    list[NormalizationField],
    Field(description="Полный список описаний и operations для полей Family; target не используется."),
]


PatternName = Annotated[
    str,
    Field(min_length=1, description="Точное имя Grok-конструкции, например SYSLOGTIMEWITHYEAR."),
]
PatternDefinition = Annotated[
    str,
    Field(
        min_length=1,
        description="Regex-тело; ссылки на другие конструкции записываются как %{ИМЯ} или %{ИМЯ:capture}.",
    ),
]
PatternMap = Annotated[
    dict[str, str],
    Field(description="Объект ИМЯ → regex-тело; это не список объектов GrokPattern."),
]
RawInput = Annotated[str, Field(description="Исходная строка целиком без изменений.")]
LayersInput = Annotated[
    list[GrokLayer],
    Field(description="Непустой список слоёв; первый source=raw, каждый следующий source — capture предыдущего слоя."),
]
StagesInput = Annotated[
    list[PipelineStage],
    Field(description="Непустой список стадий; первая source=raw, каждая следующая ссылается на поле предыдущей стадии."),
]
RecordSchemaInput = Annotated[
    list[RecordField],
    Field(description="Контракт полей, используемый для проверки required и типов integer/ipv4."),
]
ConstraintsInput = Annotated[
    list[FieldConstraint],
    Field(description="Ограничения equals/allowed_when/required_when; пустой список допустим."),
]
