from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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

    name: str = Field(description="Имя выходного поля; должно совпадать с именем Grok capture, если поле извлекается.")
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
    """Ограничение присутствия capture в зависимости от значений других captures."""

    field: str = Field(description="Имя capture, к которому применяется ограничение.")
    allowed_when: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Если field присутствует, перечисленные captures должны иметь одно из допустимых значений.",
    )
    required_when: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Если перечисленные captures имеют одно из значений, field обязан быть непустым.",
    )
    description: str = Field(description="Смысловое обоснование ограничения и наблюдения, на которых оно основано.")


class ParserDefinition(SchemaModel):
    """Набор именованных конструкций, порядок разбора и межполевые ограничения."""

    patterns: list[GrokPattern] = Field(description="Пользовательские Grok-конструкции, необходимые этому парсеру.")
    layers: list[GrokLayer] = Field(description="Слои разбора от raw к вложенным данным в порядке исполнения.")
    constraints: list[FieldConstraint] = Field(
        default_factory=list,
        description="Межполевые ограничения, которые невозможно выразить одной Grok-конструкцией без ложных комбинаций.",
    )


class Response(SchemaModel):
    """Полный результат исследования одной строки: смысл, контракт и исполненный Grok-парсер."""

    essence: str = Field(description="Кратко: участники события, действие и результат без рекомендаций и догадок.")
    structure: list[StructureLayer] = Field(description="Упорядоченные слои от RAW к значениям без выдуманной вложенности.")
    strategy: list[str] = Field(description="Только проектные решения; без альтернатив, обоснований и рекомендаций.")
    record_schema: list[RecordField] = Field(description="Минимально полный контракт выходных полей одной Family.")
    format_features: list[FormatFeature] = Field(description="Свойства формата и обязательные следствия для разбора.")
    pitfalls: list[Pitfall] = Field(description="Только неоднозначности формата, их механизм и разрешение.")
    parser: ParserDefinition = Field(description="Человекочитаемый Grok-парсер, проверенный исполнением на исходной строке.")


class ParserResult(SchemaModel):
    """Результат execute_grok_parser, который проверяется на полноту и обязательные поля."""

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
    """Прежняя версия парсера и её собственные наблюдения для независимой регрессии."""

    layers: list[GrokLayer] = Field(description="Слои прежнего парсера без изменений под новую строку.")
    patterns: dict[str, str] = Field(description="Отображение ИМЯ → regex-тело для всех конструкций прежнего парсера.")
    record_schema: list[RecordField] = Field(
        default_factory=list,
        description="Схема прежнего парсера; пустой список означает использование схемы кандидата.",
    )
    observations: list[Observation] = Field(description="Строки, на которых прежний парсер обязан сохранить результат.")
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
    layering: str = Field(description="Порядок слоёв, captures-связи и использованные Grok-конструкции.")
    output_contract: str = Field(description="Основные выходные поля, типы, обязательность и политика residual.")
    revision_reason: str = Field(description="Причина создания новой ревизии; для первой — основание создания parser.")
    regression_and_limitations: str = Field(
        description="Evidence, регрессионный результат, отрицательные примеры и известные ограничения."
    )


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
RecordSchemaInput = Annotated[
    list[RecordField],
    Field(description="Контракт полей, используемый для проверки required и типов integer/ipv4."),
]
ConstraintsInput = Annotated[
    list[FieldConstraint],
    Field(description="Межполевые ограничения allowed_when/required_when; пустой список допустим."),
]
