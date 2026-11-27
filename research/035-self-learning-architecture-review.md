# R035 — архитектура самообучающейся системы: от parser rules до собственных инструментов

> Проектный документ. Фиксирует, как текущая parser/normalization/mapping система может обнаруживать собственные ограничения, сообщать о них, развивать таксономию, normalization operations и parsing tools, а в перспективе самостоятельно создавать новые codecs. Не является отчётом о генерации кода моделью.
>
> Актуализация после реализации базового контура: SQLite Feedback Registry, инструменты `report_feedback`, `list_open_feedback`, `get_feedback`, автоматические записи runner, дедупликация и статусы `open/resolved/accepted_uncertainty` уже работают в одиночном parser/normalization/mapping pipeline. Самообучающиеся категории, автоматический reconcile и остальные механизмы ниже остаются проектом следующих этапов.

## 1. Исходная точка

Система уже умеет обучаться на данных:

```text
RAW
 → parser-агент создаёт/расширяет Grok и Family
 → normalizer создаёт descriptions и operations
 → mapper создаёт mappings в MP SIEM
 → Bundle экспортируется
 → Rust исполняет результат без LLM
```

Подтверждено на предыдущих этапах:

- parser обучается новым грамматикам и переиспользует сохранённые;
- normalization и mapping версионируются независимо;
- Taxonomy Registry поддерживает semantic search;
- Rust выполняет утверждённые правила;
- Cisco и Juniper проходят полный Python ↔ Rust parity;
- строгие `value_map`, required fields и taxonomy gates отклоняют неподтверждённые варианты.

При этом обработка нового corpus показала архитектурный пробел: **система умеет сохранять знания, но ещё не умеет системно сообщать, чего ей не хватает, и автоматически пересматривать зависимые revisions**.

Примеры:

- исходный evidence содержит только `IP`, а новый corpus добавляет `UDP` и `ICMP`;
- поле, обязательное в первом наблюдении, в другом оказывается пустым;
- timestamp не содержит год или timezone;
- числовой код можно прочитать, но нет авторитетной таблицы его значений;
- payload имеет структуру, для которой существующих codecs недостаточно.

Строгий failure в таких случаях правильнее ложного успеха. Следующий шаг — сделать из этих отказов управляемый контур обучения.

## 2. Главный принцип

```text
Самообучение происходит в control plane.
Исполнение остаётся детерминированным в data plane.
```

Control plane может:

- исследовать RAW и документацию;
- формировать гипотезы;
- сообщать о неопределённости;
- создавать новые правила и категории;
- предлагать новые инструменты;
- запускать проверки и regression.

Data plane получает только:

- versioned и проверенные правила;
- совместимые parser/normalization/mapping revisions;
- проверенные codecs и operations;
- атомарно обновляемые Bundles.

Модель не должна самостоятельно изменять ядро, sandbox, validators или политику deployment.

## 3. Что считается самообучением

Система обучается не только когда создаёт новый regex. Расширяемые библиотеки знаний:

| Библиотека | Что модель создаёт и переиспользует |
|---|---|
| Grok Registry | Именованные regex-конструкции и examples |
| Family Registry | Грамматики, record schema, constraints и dispatch |
| Normalization Registry | Описания полей и operations |
| Mapping Registry | Семантические соответствия и value_map |
| Taxonomy Registry | Собственные поля, отношения, значения, категории и адаптеры к внешним таксономиям |
| Feedback Category Registry | Типы проблем, симптомы и способы разрешения |
| Capability Registry | Operations, lookups и другие инструменты |
| Codec Registry | Детерминированные parsers структурных форматов |

Единый цикл для всех библиотек:

```text
наблюдение
 → semantic search существующего знания
 → переиспользование или новая revision
 → исполнение и независимая проверка
 → сохранение
 → применение в следующих запусках
```

Самообучение не означает бесконтрольную перезапись. Все изменения версионируются и проходят quality gates.

## 4. Feedback Registry

### 4.1. Назначение

Модель должна иметь возможность явно сказать:

- каких данных не хватает;
- какое поле или утверждение неоднозначно;
- какого инструмента не существует;
- какая конфигурация нужна;
- почему выбран conservative fallback;
- что мешает успешному завершению.

Feedback не должен теряться в prose `description`. Он сохраняется отдельно, доступен человеку и последующим агентам.

### 4.2. Два обязательных канала

**Добровольный — модель сообщает сама:**

```text
report_feedback
```

**Гарантированный — runner создаёт feedback по фактическим ошибкам:**

```text
unmapped_source_value
missing_required_field
normalization_failed
mapping_failed
invalid_taxonomy_value
missing_configuration
unsupported_operation
```

Нельзя полагаться только на самооценку модели: она может не заметить ошибку или принять гипотезу за факт.

### 4.3. Пример feedback

```json
{
  "agent": "mapper",
  "family_id": "symantec_endpoint_traffic_log_symantecserver",
  "parser_revision": 1,
  "normalization_revision": 1,
  "mapping_revision": 1,
  "field": "column_9",
  "category_id": "insufficient_observed_values",
  "category_revision": 1,
  "blocking": true,
  "summary": "Исходное evidence содержит только IP, а corpus добавляет UDP и ICMP.",
  "observed_values": ["IP", "UDP", "ICMP"],
  "needed": "Подтвердить смысл токена и пересмотреть value_map на полном corpus.",
  "status": "open"
}
```

Feedback хранит ссылки на конкретные revisions, RAW/evidence и ошибку инструмента, чтобы проблема была воспроизводимой.

## 5. Самообучающиеся категории feedback

Категории не должны быть зашитым enum. Модель формирует и развивает их как самостоятельную таксономию.

### 5.1. Инструменты

```text
semantic_search_feedback_categories
list_feedback_categories
get_feedback_category
save_feedback_category
report_feedback
list_open_feedback
resolve_feedback
```

### 5.2. Категория

```json
{
  "category_id": "insufficient_observed_values",
  "revision": 1,
  "title": "Недостаточно наблюдаемых значений поля",
  "description": "Правила построены на подмножестве значений, которое оказалось неполным на новых наблюдениях.",
  "symptoms": "Новые значения отклоняются parser, normalization или value_map.",
  "expected_evidence": "Исходные и новые значения, RAW и описание смысла поля.",
  "default_blocking": true,
  "suggested_action": "Расширить evidence и пересмотреть соответствующую revision."
}
```

Для semantic search используются название, описание, симптомы и ожидаемые доказательства.

### 5.3. Правила создания

Перед новой категорией модель обязана:

1. выполнить semantic search;
2. проверить близкие определения;
3. создать новую только при отсутствии подходящей;
4. задать описание, симптомы, примеры и ожидаемое разрешение;
5. не переписывать старую категорию при изменении смысла — создать revision.

`default_blocking` является рекомендацией. Конкретная запись может изменить его с обоснованием.

### 5.4. Жизненный цикл feedback

```text
open → in_progress → resolved
                   → accepted_uncertainty
```

Resolution должен содержать:

- созданные revisions;
- объяснение решения;
- результат regression;
- остаточную неопределённость, если она сохраняется.

Повторные проблемы дедуплицируются по agent/family/field/category, а не создают бесконечный поток одинаковых записей.

## 6. Не путать missing tool и missing knowledge

Пример ICMP type/code:

```text
Значение "3" уже прочитано.
```

Для преобразования в текст `value_map` достаточно. Не хватает не вычислительного инструмента, а:

- подтверждения, что поле действительно является ICMP type;
- авторитетной таблицы type/code;
- правила поведения для неизвестного кода.

Feedback должен отражать это различие:

```json
{
  "category_id": "missing_authoritative_reference",
  "blocking": false,
  "current_fallback": "Сохранить число в numfield без выдуманной текстовой трактовки.",
  "needed": "Vendor/IANA reference и подтверждение семантики поля."
}
```

Если справочник найден, существующий `value_map` может быть достаточен. Новый код требуется только тогда, когда текущий DSL не выражает операцию.

## 7. Автоматический reconcile зависимых revisions

### 7.1. Правило

При изменении parser автоматически запускается проверка всей цепочки:

```text
reconcile_family(parser_revision)
```

Но новые normalization и mapping revisions создаются только при реальной необходимости.

### 7.2. Сценарии

**Изменился regex, но captures и смысл прежние:**

- parser regression;
- normalization на старом и новом evidence;
- mapping на старом и новом evidence;
- при успехе — детерминированный rebase существующих правил на новую parser revision без LLM.

**Изменился набор или смысл captures:**

- normalizer пересматривает descriptions, required и operations;
- mapper пересматривает targets после новой normalization revision;
- старые правила не активируются с новым parser до проверки.

**Добавилось значение существующего поля:**

- parser может остаться прежним;
- mapping расширяет `value_map` либо явно фиксирует неопределённость;
- normalization меняется только при необходимости изменить представление.

**Новая Family:**

- всегда требуется совместимая normalization и mapping перед полным MP SIEM Bundle.

### 7.3. Атомарная активация

```text
candidate parser/normalization/mapping
 → старые и новые evidence
 → все обязательные проверки
 → новый Bundle
 → атомарная замена Registry
```

Недопустимо разворачивать новый parser со старой несовместимой цепочкой. До успеха старый Bundle остаётся активным, новые непокрытые записи идут в quarantine.

### 7.4. Отличие от текущего runner

Текущий runner в основном создаёт revisions при их отсутствии. Он не обязан автоматически реагировать на последующие `mapping_failed` и не выполняет полный reconcile. Это один из первых практических компонентов для реализации.

## 8. Capability Registry

Capability — переиспользуемая операция, а не частный код одной Family.

Примеры:

- `parse_timestamp`;
- `normalize_ip`;
- `value_map`;
- `lookup_value`;
- `url_decode`;
- `base64_decode`;
- `normalize_protocol_number`.

Каждая capability содержит:

```text
name@revision
description
input/output schema
params schema
error contract
examples и negative cases
Python implementation или DSL definition
Rust implementation или DSL definition
validation evidence
compatibility hash
```

Агент сначала ищет существующую capability. Только если DSL не выражает задачу, создаётся новая.

## 9. Самостоятельное создание CEF-codec

CEF показывает, как система может выйти за пределы исходных инструментов.

### 9.1. Наблюдение ограничения

На payload видны:

```text
CEF:0|Vendor|Product|...|key=value key=value
```

Со временем накапливаются проблемы Grok:

- `\|`;
- кавычки;
- escape-последовательности;
- пробелы в значениях;
- произвольные ключи extension;
- пустые поля.

Модель регистрирует feedback: нужен детерминированный codec вместо расширения гигантского regex.

### 9.2. Спецификация

```json
{
  "codec": "candidate_cef",
  "input": "CEF:<version>| и последующий текст",
  "header": "Семь полей, разделённых неэкранированным |",
  "extension": "key=value с quoting и escaping",
  "output": "Строковые fields, residual и конкретные ошибки",
  "failure_policy": "Никакого частичного успеха при нарушении грамматики"
}
```

### 9.3. Реализация и доказательство

Модель создаёт Python и Rust variants и проверяет:

- одинаковые fields/residual на одном RAW;
- escaped `\|`, `\=`, `\\`;
- Unicode;
- кавычки и пустые значения;
- дубли ключей;
- недостающие и лишние разделители;
- negative cases и fuzzing;
- отсутствие panic и необработанных исключений;
- regression существующих Families.

После этого сохраняется `cef@1`, который будущие parser-агенты находят семантически:

```text
model Grok envelope → cef@1 payload
```

### 9.4. Необходимое изменение текущего кода

Сейчас codecs закрыты перечислениями в prompt, `PipelineStage`, `try_parse` и Rust enum. Поэтому текущая система не может добавить новый codec только через Registry.

Нужен единый Codec Registry, в котором имя кодека ссылается на revision реализации. Миграция существующих CEF/CSV/KV в Registry станет первым доказательством открытой модели расширения.

## 10. DSL прежде генерации кода

Большинство новых возможностей должно выражаться декларативно:

```json
{
  "op": "lookup_value",
  "params": {
    "dictionary": "icmp_type",
    "on_missing": "error"
  }
}
```

Преимущества:

- нет произвольного исполнения кода модели;
- проще Python ↔ Rust parity;
- легче валидировать params и ошибки;
- проще сериализация Bundle;
- меньше security и production-рисков.

Генерация Python/Rust оправдана только для нового класса операций, который DSL не покрывает.

## 11. Безопасная генерация кода

Если модель всё же пишет код, обязательны:

- sandbox без секретов и production filesystem;
- запрет свободной сети по умолчанию;
- ограничения CPU, памяти и времени;
- независимые positive/negative/adversarial tests;
- fuzzing;
- Python ↔ Rust parity;
- versioned releases и rollback;
- запрет изменять validators, sandbox и deployment policy;
- отдельный approval для новых capabilities.

Генератор не должен иметь возможности ослабить тесты, под которые проверяется его собственная работа.

Полностью автономная перезапись ядра теоретически возможна, но не является разумной целью. Цель — **саморасширяющаяся библиотека при неизменном защищённом ядре**.

## 12. Роль внешнего поиска

DDGS может помогать находить документацию, но фактические вызовы показали нестабильность внешних backends: timeout и отсутствие результатов.

Планируемая замена — SearXNG в Docker:

- собственный endpoint;
- предсказуемый JSON API;
- управляемые backends и timeouts;
- сохранение запросов и ответов как research evidence.

Поиск не заменяет corpus. Он помогает подтвердить смысл, но покрытие доказывается полным исполнением наблюдений.

## 13. Что не должно попадать в Rust Bundle

```text
Открытые сомнения
Непроверенные гипотезы
Feedback и категории проблем
Поисковые запросы и рассуждения
Candidate tools без validation
Несовместимые revisions
```

В Bundle попадают только проверенные исполняемые правила и ссылки на их revisions.

## 14. Уровни автономии

Рекомендуется разделять:

1. **Knowledge automation** — Grok, Families, normalization, mapping. Уже реализовано.
2. **Feedback automation** — система сообщает и классифицирует ограничения.
3. **Reconcile automation** — автоматический пересмотр цепочки и атомарный redeploy.
4. **Declarative capabilities** — новые operations через DSL без кода.
5. **Generated codecs** — ограниченная генерация с sandbox и независимыми проверками.
6. **Core modification** — не включать в автономный контур.

Это позволяет наращивать самообучение без немедленного перехода к рискованной генерации кода.

## 15. Самообучающаяся таксономия событий

До сих пор mapper выбирал targets в заранее заданной MP SIEM 8.0. Более общий вариант — система сама проектирует и развивает каноническую таксономию, а MP SIEM становится одним из внешних адаптеров.

### 15.1. Зачем собственная таксономия

Она нужна, когда:

- внешняя схема не содержит подходящего поля;
- один и тот же смысл встречается у разных vendors;
- generic datafields начинают заменять устойчивую модель данных;
- требуется сохранить vendor-specific знание без потери общей семантики;
- одна normalized Family должна экспортироваться в несколько внешних схем.

Внутренняя таксономия не должна незаметно переписывать официальный MP SIEM. Расширения MP SIEM оформляются отдельным namespace и явной версией.

### 15.2. Из чего состоит

```text
Taxonomy
 ├── fields и stable IDs
 ├── scalar/compound types и units
 ├── entity relations
 ├── event categories как DAG/дерево
 ├── enum values и aliases
 ├── vendor extensions
 ├── external mappings
 └── revisions и compatibility policy
```

Пример узла:

```json
{
  "taxonomy": "canonical_security_event",
  "revision": 3,
  "field_id": "network.source.user.name",
  "type": "string",
  "description": "Имя учётной записи, связанной с исходной стороной сетевого взаимодействия.",
  "examples": ["alice", "NETWORK SERVICE"],
  "aliases": ["username", "source_user"],
  "external_mappings": ["mp_siem_8.0:subject.account.name"]
}
```

### 15.3. Как модель её развивает

```text
Новые NormalizationField descriptions
 → semantic search существующих узлов
 → reuse / add / split / merge / deprecate
 → проверка mappings и regressions
 → новая Taxonomy revision
```

Модель может создавать узлы по повторяющимся смыслам, а не по названиям captures. Например, `username`, `User Name` и `account` из разных источников могут относиться к одному понятию только при совпадении описаний и роли в событии.

### 15.4. Допустимые операции развития

- **add** — добавить новое понятие с evidence;
- **extend_enum** — добавить подтверждённое значение;
- **split** — разделить слишком широкое понятие;
- **merge** — объединить дубликаты с сохранением aliases;
- **move** — изменить иерархию без изменения stable ID;
- **deprecate** — прекратить использование без удаления истории;
- **retype** — изменить тип только с миграцией и regression;
- **map_external** — связать узел с MP SIEM/OCSF/ECS или другой схемой.

### 15.5. Quality gates

- каждый узел имеет описание, тип, примеры и evidence;
- нет циклов в иерархии;
- нет семантически неразличимых дубликатов;
- aliases не меняют смысл;
- обязательные поля имеют источник либо явный reason отсутствия;
- enum extension не подменяет неизвестные значения default;
- merge/split не ломают старые mappings;
- внешняя схема не смешивается с собственным namespace;
- изменение таксономии вызывает mapping reconcile и полный Bundle regression.

### 15.6. Зависимости

Изменение Taxonomy revision может требовать пересмотра mapping даже при неизменном parser и normalization. Поэтому dependency graph расширяется:

```text
Codec → Parser → Normalization → Canonical Taxonomy → Mapping adapter → Bundle
```

External MP SIEM mapping — один из adapters, а не обязательное ядро смысловой модели.

## 16. Развитие инструментов нормализации

Normalizer должен не только выбирать из семи operations, но и развивать библиотеку преобразований.

### 16.1. Источник новой operation

```text
Normalizer feedback: unsupported_transform
 → поиск существующей capability
 → попытка выразить через DSL
 → новая Operation revision только при необходимости
```

Примеры наблюдаемых потребностей:

- protocol number → имя;
- ICMP type/code → имя;
- MAC normalization;
- URL/HTML decoding;
- units и числовые суффиксы;
- vendor timestamp;
- timezone и year из ingestion metadata;
- dictionary lookup;
- controlled null semantics.

### 16.2. Сначала данные, потом код

Dictionary-driven операции не требуют отдельной реализации для каждого vendor:

```json
{
  "op": "lookup_value",
  "params": {
    "dictionary": "iana_protocol_numbers@1",
    "on_missing": "error",
    "keep_original": false
  }
}
```

Сама dictionary — отдельная versioned capability с источником, значениями, aliases и evidence. Модель может расширять её без изменения runtime.

### 16.3. Контракт новой Operation

```text
name@revision
description и границы применимости
input/output JSON types
params schema и defaults
null/zero policy
unknown-value policy
error contract
positive/negative examples
Python ↔ Rust parity
performance/resource limits
```

Например, `lookup_value` обязан различать отсутствие ключа, `null`, пустую строку и валидный ноль. Неизвестное значение не должно молча сохраняться или заменяться наиболее вероятным текстом.

### 16.4. Составные operations

Сначала модель пытается собрать pipeline существующих operations:

```text
trim → null_if → to_integer → validate_range → lookup_value
```

Новая составная operation оправдана только если её нельзя безопасно выразить такой цепочкой или если она становится устойчивой независимой capability.

### 16.5. Совместимость Bundle

Bundle должен явно хранить versions operations и dictionaries. Rust при загрузке отклоняет неизвестную или несовместимую revision. Изменение operation запускает:

```text
operation regression
 → normalization regression всех зависимых Families
 → mapping regression
 → Python ↔ Rust parity
 → атомарный Bundle update
```

## 17. Развитие инструментов парсинга

Parser расширяется на двух уровнях:

1. новые Grok-конструкции и Family rules;
2. новые переиспользуемые parsing capabilities.

### 17.1. Parser Tool Registry

В Registry могут входить:

- envelope probes;
- tokenizers;
- delimiter/quote/escape scanners;
- CEF/LEEF/KV/CSV/JSON/XML codecs;
- multiline assemblers;
- nested payload extractors;
- field projection и split operations;
- partial-stage execution tools;
- deterministic routing probes;
- linear/DFA-compatible matchers.

Это не обязательно полноценные кодеки. Например, отдельный scanner `split_unescaped(delimiter='|', escape='\\')` может использоваться CEF и другими форматами.

### 17.2. Как появляется новый parsing tool

```text
Повторяющаяся parser failure
 → Feedback Category
 → анализ общего класса форматов
 → спецификация входа/выхода/ошибок
 → composition существующих tools или новый codec
 → positive/negative/fuzz tests
 → Python ↔ Rust parity
 → Parser Tool revision
 → Family evaluation и полный downstream regression
```

Модель должна строить инструмент для класса форматов, а не частный parser одной строки.

### 17.3. Пример: CEF и KV

CEF начинается с общего scanner неэкранированных `|`, затем header parser и extension tokenizer. KV начинается с row tokenizer с quoting/escaping, после чего применяются `pair_separator`, `key_value_separator`, positional names и duplicate policy.

Это позволяет строить новые codecs композицией:

```text
scan_delimited_row
 → split_first_separator
 → unescape
 → assign_fields
 → validate_residual
```

### 17.4. Contract parsing tool

```text
name@revision
input grammar и encoding
output fields/residual
parameters и defaults
error/failure policy
nesting и source contract
complexity и resource limits
positive/negative/fuzz evidence
Python/Rust implementation или DSL
compatibility hash
```

Parser tool обязан отклонять неполный разбор и не терять residual. Он не должен угадывать semantic mapping полей.

### 17.5. Dispatch тоже развивается

При увеличении числа Families система может добавлять новые routing capabilities:

```text
literal anchors → Aho–Corasick
linear routing regex → RegexSet
stable captures → exact message keys
compiled DFA-safe probes → deterministic candidate filter
```

Эти инструменты выбирают candidates, но не заменяют полный parser. Их correctness доказывается отсутствием false negatives на полном corpus.

### 17.6. Общий dependency graph

```text
Parser Tool/Codec revision
 → parser revisions
 → normalization revisions и Operation revisions
 → Taxonomy revision
 → Mapping adapters
 → Bundle
```

Любое изменение должно запускать только затронутый подграф, но активация остаётся атомарной.

## 18. Практический порядок реализации

1. Feedback Registry и вывод open/blocking вопросов в runner.
2. Модельные feedback categories с semantic search и revisions.
3. Автоматический feedback из детерминированных ошибок.
4. `reconcile_family` и атомарная активация Bundles.
5. Capability/Operation Registry для декларативной нормализации.
6. Versioned dictionaries и lookup operations.
7. Parser Tool/Codec Registry и миграция существующих CEF/CSV/KV.
8. Canonical Taxonomy Registry и adapters к MP SIEM.
9. Dependency graph для автоматического downstream regression.
10. Sandbox для первого generated codec/operation.
11. SearXNG как управляемый источник внешних references.

## 19. Критерии успеха

Для каждой активной Family:

- нет неучтённых parser/normalization/mapping failures;
- все blocking feedback либо resolved, либо Family не активируется;
- старые и новые evidence проходят;
- Python ↔ Rust parity сохраняется;
- открытая неопределённость видна человеку;
- повторная проблема переиспользует существующую feedback category;
- новая capability имеет schema, examples, errors и regression;
- новая taxonomy node имеет stable ID, описание, тип, evidence и external mappings;
- изменение operation запускает regression всех зависимых normalization revisions;
- изменение codec запускает parser и полный downstream regression;
- изменение taxonomy запускает mapping adapter regression;
- откат на предыдущий Bundle возможен без изменения данных.

## 20. Чего не хватает для полностью автономной системы

Главное недостающее звено — не ещё одна модель, а замкнутый orchestrator, который сам проходит путь от ошибки до исправления, regression и повторного запуска.

### 20.1. Целевой контур

```text
RAW → Rust
   → ошибка/no_family → quarantine
   → Feedback Registry
   → нужный агент
   → reconcile parser/normalization/mapping/taxonomy
   → полный regression
   → новый Bundle
   → атомарное обновление Rust
   → повторная обработка
```

Сейчас между этапами ещё остаются ручные действия: вызов агента, экспорт Bundle, исправление mapping, запуск Rust и анализ ошибок.

### 20.2. Недостающие компоненты

1. **Feedback Registry** — добровольные сообщения модели и автоматические задачи из `mapping_failed`, `normalization_failed`, parser errors и missing configuration.
2. **Автоматический repair loop** — повторное исследование при несовместимом сохранённом mapping/normalization, а не только при отсутствии parser. Работа продолжается до полного corpus coverage без выбора удобных примеров.
3. **`reconcile_family` и dependency graph** — пересборка только затронутых revisions и запрет частичной активации несовместимой цепочки.
4. **Открытые Registries** — Feedback Categories, Canonical Taxonomy, Operations/dictionaries, parsing tools и codecs развиваются моделью.
5. **Evidence Manager** — автоматическое накопление старых/новых наблюдений, значений полей, negative cases и причин отказов. Один удачный пример не доказывает обученность Family.
6. **Атомарный deployment и rollback** — versioned Bundle Registry, переключение целиком и откат без разработчика.
7. **Надёжный long-running runner** — resume, идемпотентность, управление параллельностью, повтор после сетевого сбоя и сохранение состояния. Прерванный прогон не должен требовать ручного восстановления контекста.
8. **Sandbox для новых инструментов** — ограниченное исполнение, независимые тесты, fuzzing и Python ↔ Rust parity без права модели менять validators.
9. **Reference Registry и ingestion metadata** — управляемый поиск, например SearXNG, и внешние год/timezone вместо их угадывания.
10. **Политика автономности** — явные правила автоматической активации, `accepted_uncertainty` и остановки. Generated code требует более строгого контура, чем data-only rules.

### 20.3. Минимальное определение автономности

Систему можно считать автономной в заданных границах, когда без ручного вмешательства она:

- обнаруживает проблему;
- классифицирует её;
- выбирает или создаёт revision;
- проверяет старые и новые evidence;
- обновляет runtime;
- повторяет обработку;
- сохраняет видимую неопределённость, если доказательств недостаточно.

При этом предпочтительный путь — расширение правил и DSL, а не самостоятельная перезапись защищённого ядра.

## 21. Главный вывод

Система может стать самообучающейся не только на уровне парсеров. Она может постепенно формировать:

- грамматики входных форматов и parsing tools;
- правила нормализации, operations и versioned dictionaries;
- собственную каноническую таксономию и внешние mapping adapters;
- классификацию собственных затруднений;
- библиотеку codecs и проверенных инструментов.

Ключевое условие:

> **Модель расширяет знания и capabilities, но независимое ядро проверки и исполнения остаётся ограниченным, версионированным и подконтрольным.**

[← R033](033-juniper-full-rust-review.md) · [Все исследования](README.md)
