# R037 — Logicore/Saturn: production-контракт для генерации `mp_normalizer`-правил

> Обзор выполнен 2026-11-25. Осмотрены все восемь указанных репозиториев, исходники Ruby/Rust, VRL stdlib двух версий, TEXT grammar, текущие pipelines и `saturn-tools`. Дополнительно выполнены нативные прогоны правил через `libmpsiem.so` из образа `saturn:v1.1.7` и проверка Logstash Grok.
>
> Цель: определить, как существующий parser/normalizer/mapper агент должен выпускать `norm_rules_<source>.yml` и отдельный `<source>.conf` для TEXT-источников, и как проверять их через реальный Saturn.

## 1. Краткий итог

- Production-результат: **YAML с `kind: TEXT` + отдельный fallback CONF по модели AxelNAC**, а не обязательная интеграция в `client_parsers`/`client_settings`.
- Существующие три агента должны оставаться источником смысловой модели. Финальные YAML/VRL/CONF должен собирать **детерминированный компилятор**, а не свободный ответ LLM.
- Для parser-агента нужен отдельный target profile **MP TEXT DSL**. Текущий `%{CUSTOM_REGEX:capture}` не совпадает с MP pattern syntax и не переносится автоматически.
- `mp_normalizer` и Logstash-фильтр `vrl` — разные runtimes с разными версиями VRL. Поддержку функций проверяем только на `mpsiem`, а не на `vrl_executor`.
- Набор функций и токенов зависит от версии `mpsiem`. Образ `saturn:v1.1.7` отстаёт от текущего HEAD: в нём нет `mp_json` и семи новых TEXT-токенов.
- Успешный YAML не гарантирует, что событие дойдёт до него. Текущий generic `Default/Syslog-3164` перехватывает AxelNAC-подобную строку и присваивает `id=syslog-3164`; fallback из MR после этого уже не нужен/не вызывается.
- Отладка делается через `saturn-tools` с файловой подменой `docker/saturn-pipelines`. На текущем Windows-окружении вложенный `git clone` в submodule-path дал пустые bind mounts; обычная файловая копия сработала. `entrypoint.sh` требует LF и не пробрасывает `"$@"`.

## 2. Репозитории и зафиксированные версии

| Репозиторий | HEAD при обзоре | Роль в задаче |
|---|---|---|
| `logiq` | `7f1210545262dd278c809ebe4d752b4f7f1b0512` | Установка продукта, Helm/Ansible, первичная доставка pipelines в S3 |
| `saturn` | `36de9013772ee2992779e2a09b667a02ae733172` | Образ Logstash, Ruby plugins, native gems, migrator и ClickHouse taxonomy |
| `saturn-pipelines` | `0fce350fc93891e1e7fa7c01f191a0cbf619233f` | Актуальные `.conf`, `pipelines.yml`, базовые YAML |
| `saturn-pipelines` MR 9 | `44c76d43573c2262efa2f577e2417a24d97fb99f` | AxelNAC YAML + fallback CONF |
| `saturn-tools` | `7652c6d35f4c32c5ac9c488320dee1b1bc88e562` | Локальный стенд, отправка RAW, сравнение результатов в ClickHouse |
| `mpsiem` | `dc0592d0bc8b950b0276b9965678a64735a874f0` | Основной исполнитель normalization/filtration rules |
| `events_aggregator` | `7a44fcb754fd838fe1a6ade5cfb07465660fc2f6` | Агрегация auditd перед нормализацией; VRL не используется |
| `vrl_executor` | `4a86ed8549615859af90e6eba41db17dc46befee` | Отдельный Logstash `vrl` filter, другой VRL runtime |
| `mpsiem-xp-converter` | `88f0e6a6c853f153343ca69cb4236286adcc2a80` | XP AST и преобразование XP formulas в VRL/YAML |

### Runtime для отладки

Образ: `nexus.inf.axel.pro/docker-group/logiq/saturn:v1.1.7`.

Метки образа:

- Saturn revision: `98c36249d068b9ec375da307c628c16e802afd2a`;
- `mpsiem`: `ffd58d2818ef850ac780f74c24d9a4e0b295c819`;
- `events_aggregator`: `7a44fcb754fd838fe1a6ade5cfb07465660fc2f6`;
- `vrl_executor`: `4a86ed8549615859af90e6eba41db17dc46befee`.

Следовательно, **HEAD репозитория и фактический debug/production runtime нужно версионировать раздельно**. Нельзя считать возможности HEAD автоматически доступными в образе.

## 3. Границы компонентов

### 3.1 `mp_normalizer`

Ruby plugin в Saturn:

- `rules_filepath` — обязательный;
- `body_field` — по умолчанию `message`;
- `mime_type_field` — по умолчанию `mime`;
- `tag_on_failure` — по умолчанию `normalizer_failure`;
- `tag_on_unparsed` — по умолчанию `unparsed`;
- `pass_fields` — дополнительные поля, передаваемые в native normalizer;
- `show_stats` — статистика времени обработки.

В native вызов передаются только `message`, `mime` и `pass_fields`. Полученные поля записываются обратно **поверх исходного Logstash event**, поэтому исходные `message`, `mime`, `host`, `type`, tags и остальные Logstash-поля сохраняются.

`Filewatcher` перечитывает YAML. Новый Normalizer сначала создаётся, затем заменяет старый; старый освобождается через 120 секунд. При ошибке создания нового экземпляра сохраняется старый.

FFI `normalizer_create` вызывает `normalizer::from_yaml(..., false)`: production loader **не strict**. Ошибочное правило может быть пропущено с `warn!("cannot append rule")`, а остальные продолжат работать. Поэтому для генератора нужен отдельный strict-validator; успешный запуск plugin не доказывает, что все правила загружены.

### 3.2 Logstash `vrl` / `vrl_executor`

Это отдельный plugin:

```conf
vrl {
  fields => [...]
  code => '...'
  tag_on_exception => "_vrlexception"
}
```

Он компилирует только `vrl::stdlib::all()` и не регистрирует `mp_*`. Ошибка выполнения одного события превращается в `null` и затем в `tag_on_exception`.

**Он не проверяет ни MP TEXT pattern, ни `mp_*`, ни поведение `mp_normalizer`.**

### 3.3 `events_aggregator`

- VRL не используется.
- Использует `grok-rs` и другой syntax: `%{INT:field}`, `%{NOTSPACE:field}`, `(?<source>...)`.
- Нужен для aggregation auditd в `input_syslog`, а не для генерации TEXT normalization rules.
- Его Grok нельзя использовать как доказательство совместимости с MP TEXT DSL.

### 3.4 `mpsiem-xp-converter`

Полезен как существующий источник AST/codegen:

- assignments и field flattening `.` → `_`;
- `if/elif/else` → VRL blocks с `mp_bool`;
- `switch` → `switch_hash` либо if/else;
- `drop` → `abort`;
- XP function call → `mp_<name>`;
- `coalesce` → `mp_coalesce([...])`;
- `submessage` → `. = mp_submessage(...)`;
- comparisons/math → `mp_eq`, `mp_ne`, `mp_add` и т.д.

Ограничения:

- это не capability registry и не runtime validator;
- имена функций транслируются механически, поэтому неизвестная `mp_*` появится только при native compile;
- conversion errors помещаются в report, а неуспешные правила исключаются из YAML;
- normalization fixtures полезны как примеры codegen, но не заменяют execution tests.

### 3.5 `logiq`

Отвечает за развёртывание, а не за syntax правил. `saturn-pipelines` загружается в S3 через `s3-create-bucket`. Копирование выполняется **только если bucket пуст**. Поэтому обновление репозитория само по себе не обновляет уже существующую установку. Для локальной отладки это обходим через bind mounts `saturn-tools`.

## 4. VRL: точные возможности по версиям

| Runtime | VRL | Standard library | MP SIEM functions |
|---|---:|---:|---:|
| `mpsiem` HEAD `dc0592d0` | `0.27.0` | 196 | 85 + `mp_submessage` при наличии subformulas |
| `mpsiem` в `saturn:v1.1.7` (`ffd58d28`) | `0.27.0` | 196 | 84 + `mp_submessage` при наличии subformulas |
| `vrl_executor` `4a86ed85` | `0.22.0` | 187 | нет |

Между VRL stdlib `0.22.0` и `0.27.0` добавлены девять функций, удалений нет:

```text
DecodeLz4
DecryptIp
EncodeLz4
EncryptIp
Haversine
HttpRequest
Pop
ValidateJsonSchema
Xxhash
```

В runtime `saturn:v1.1.7` по сравнению с HEAD отсутствует `mp_json`.

### 4.1 VRL instructions и поведение

Для генерации можно использовать обычный VRL:

- assignments: `."field" = value`;
- paths и flat names: `."src_ip"`, `."$temporary"`;
- local variables: `vrl_tmp_x = null`;
- arrays/objects и indexed access;
- `if / else if / else` и blocks;
- fallible calls с `!`, например `get!`, `string!`, `parse_timestamp!`;
- `del`, `exists`, `get`, `length`;
- `abort`.

Особенности execution:

- `condition` выполняется на `event + captures`; запись в condition не сохраняется в событии. Ошибка condition означает `false`.
- `abort` в `body` или `appendix` → `Unparsed`.
- Другая VRL runtime error → `Failure` и `tag_on_normalizer_failure`.
- После body выполняется `normalize_value`, затем `appendix`.
- `mp_submessage` регистрируется только для правила с subformulas; без них вызов не скомпилируется.
- `mp_table_search` относится к filtrator/lookup tables, а не к обычному normalizer body.

### 4.2 Полезные Vector stdlib functions

Подтверждены в исходниках `vrl-0.27.0`:

- время: `parse_timestamp`, `format_timestamp`, `to_unix_timestamp`, `from_unix_timestamp`, `now`;
- parsing: `parse_grok`, `parse_groks`, `parse_regex`, `parse_regex_all`, `parse_json`, `parse_csv`, `parse_key_value`, `parse_syslog`, `parse_cef`, `parse_xml`;
- strings: `strip_whitespace`, `downcase`, `upcase`, `starts_with`, `ends_with`, `contains`, `replace`, `split`, `join`, `length`;
- types: `string`, `to_string`, `to_int`, `to_float`, `to_bool`, `is_string`, `is_integer`, `is_ipv4`, `is_ipv6`, `is_null`, `exists`;
- data: `get`, `del`, `keys`, `values`, `merge`, `flatten`, `unflatten`, `compact`, `unique`, `slice`;
- misc: `get_env_var`, `log`, `assert`, `uuid_v4`, `uuid_v7`.

Важные различия:

```vrl
string!(x)       # assertion: x уже должен быть строкой; это НЕ общее преобразование
 to_string!(x)    # преобразование скаляров в строку; arrays/objects вызывают ошибку
```

`parse_timestamp!` поддерживает:

```vrl
parse_timestamp!(value, format: "%d/%m/%Y %H:%M:%S", timezone: "Europe/Paris")
```

Если timezone не указана, используется timezone VRL Context. `parse_timestamp!` возвращает Timestamp и является fallible.

`parse_grok!` использует стандартный Vector Grok (`%{TIMESTAMP_ISO8601:field}`), а **не MP TEXT DSL**. Это ещё одна причина не смешивать два синтаксиса.

### 4.3 Зарегистрированные MP SIEM functions

Список получен из `mpsiemlib_all()`, а не из документации.

**Analyze (8):**

```text
mp_coalesce
mp_flip_endianness16 / 32 / 64
mp_in_subnet
mp_clear_bit
mp_check_bit_mask
mp_get_bit
mp_set_bit
```

**Lists (9):**

```text
mp_append
mp_extend
mp_in_list
mp_intersect
mp_is_list
mp_remove
mp_slice
mp_sort
mp_unique
```

**Text (16):**

```text
mp_csv
mp_find_substr
mp_keyvalue
mp_length
mp_lower
mp_match
mp_regex
mp_regex_match
mp_replace
mp_rot13
mp_split
mp_strip
mp_strip_all
mp_substr
mp_upper
mp_json   # только HEAD, отсутствует в saturn:v1.1.7
```

**Datetime (17):**

```text
mp_datetime / mp_maybe_datetime
mp_datetime_to_epoch / mp_datetime_to_epoch_ms / mp_datetime_to_win_ticks
mp_duration / mp_maybe_duration
mp_epoch_to_datetime / mp_epoch_ms_to_datetime
mp_hour / mp_minute / mp_second
mp_timezone
mp_win_ticks_to_datetime
mp_year / mp_month / mp_day
```

**Type conversion (19):**

```text
mp_bool
mp_buffer_from_base64
mp_decode
mp_hex_to_string
mp_ipv4 / mp_maybe_ipv4
mp_ipv6 / mp_maybe_ipv6
mp_join
mp_macaddr / mp_maybe_macaddr
mp_number / mp_maybe_number
mp_number8 / mp_maybe_number8
mp_number16 / mp_maybe_number16
mp_string
```

**Base operations (15):**

```text
mp_add / mp_sub / mp_mul / mp_div / mp_mod
mp_not / mp_uminus
mp_and / mp_or
mp_le / mp_lt / mp_ge / mp_gt / mp_eq / mp_ne
```

**Custom:**

```text
switch_hash
mp_submessage  # conditionally registered per rule with subformulas
```

Семантика не совпадает с Vector one-to-one:

- `mp_lower` нестроковое значение превращает в `null`;
- `mp_number` делает MP-compatible parsing, включая частичный numeric prefix и специальные знаки;
- `mp_string` округляет float до ближайшего integer и форматирует Timestamp с milliseconds;
- `switch_hash(condition, absent, [keys], values...)` возвращает `absent` при неизвестном значении. Наш строгий `value_map` не должен молча использовать `absent=null`, если неизвестное значение должно быть ошибкой.

## 5. MP TEXT DSL

### 5.1 Токены HEAD

В текущем `mpsiem` зарегистрировано 35 токенов:

```text
BASE64
CSV
DATETIME
DATETIME_CHECKPOINT_OPSEC
DATETIME_ISO8601
DATETIME_MMDDYYYY_HHMMSS
DATETIME_MMDDYYYY_HHMMSS_NOTZ
DATETIME_SAP
DATETIME_SAP_PO
DATETIME_SEDO
DATETIME_SYSLOG
DATETIME_SYSLOG_CYRILLIC
DATETIME_SYSLOG_CYRILLIC_NOTZ
DATETIME_SYSLOG_HUAWEI
DATETIME_SYSLOG_HUAWEI_CYRILLIC
DATETIME_SYSLOG_NEXUS
DATETIME_SYSLOG_NEXUS_NOTZ
DATETIME_SYSLOG_NOTZ
DATETIME_WINDOWS_CIM
DATETIME_YYYYMMDD_HHMMSS
DURATION
HOSTNAME
IPV4
IPV6
JSON
KEYVALUE
LITERAL
MACADDR
NTUSER
NUMBER
REST
STRING
UNTIL
WORD
WORDDASH
```

В `saturn:v1.1.7` доступны только 28. Отсутствуют:

```text
DATETIME_MMDDYYYY_HHMMSS_NOTZ
DATETIME_SYSLOG_CYRILLIC
DATETIME_SYSLOG_CYRILLIC_NOTZ
DATETIME_SYSLOG_HUAWEI_CYRILLIC
DATETIME_SYSLOG_NEXUS_NOTZ
DATETIME_SYSLOG_NOTZ
JSON
```

### 5.2 Синтаксис

```text
{NUMBER}                         обязательный токен без capture
{src.ip=IPV4}                    taxonomy field, сохраняется как src_ip
{$user=STRING}                   temporary variable
{$user=UNTIL("]")}
{time=DATETIME_SYSLOG}
{"[" $procid=NUMBER "]"}?       optional group
{$x=WORD+}                       one or many
{$x=WORD*}
{$x=WORD??}                      lazy optional
{$x=IPV4|$x=IPV6|}              alternatives, последняя пустая = optional
```

Поддерживаются:

- literals в одинарных/двойных кавычках;
- groups и alternatives;
- `?`, `+`, `*`, `??`, `+?`, `*?`;
- `UNTIL("literal")` и аргументы других токенов;
- полное совпадение события после trimming MP-blank по краям.

Пробел в pattern **не означает ровно один пробел**: между элементами пропускается серия MP-blank любой длины, включая нулевую. MP-blank включает обычные whitespace, NBSP, несколько Unicode spaces и BOM. `STRING` заканчивается только на `' '`, `\t`, `\n`, `\r`; другие Unicode blanks входят в значение.

Приоритет разбора определяется внутри одного правила. Между разными подошедшими правилами порядок не специфицирован; Normalizer выбирает первое, чьё condition прошло. Поэтому правила генератора должны быть разграничены pattern/condition, а не полагаться на порядок YAML.

### 5.3 Captures и типы

- `Id` с `.` автоматически превращается в `_`: `{event_src.ip=...}` → `event_src_ip`.
- `$variables` удаляются из итогового события в `normalize_value`.
- `NUMBER` имеет postcapture и становится числом.
- `DATETIME*` становится Timestamp.
- `normalize_after_grok_text` дополнительно приводит известные taxonomy fields перед body; `$variables` пропускает.
- `normalize_value` после body удаляет `null` и `$variables`, приводит Number/DateTime/IP/MAC/Bool, а DateTime сериализует обратно в строку.
- Поле с неизвестным типом приводится к string по MP rules, а не по Vector `to_string`.

`config/patterns/grok_patterns.txt` не передаётся в `mp_normalizer`: Ruby plugin не имеет такого параметра. Пользовательские Grok definitions из файла нельзя считать доступными MP-токенами.

## 6. Taxonomy и ClickHouse

Таксономия Logicore наследуется из таксономии MP SIEM: это её подмножество, дополненное техническими полями Logicore. Поэтому семантику полей, категорий и enum-значений нужно брать из таксономии MP SIEM и подтверждённых production-правил, а не выводить из SQL-схемы.

Актуальный источник допустимых storage-полей и их физических типов: `saturn/migrator/db/migrations/schemas/mpsiem`. Migrator отвечает на вопросы «какое плоское имя поля существует и как оно хранится», но не является самостоятельным источником семантической таксономии.

Там преимущественно `Nullable(String)`, но также есть:

- `Nullable(Int64)` для ports, durations, counts, numfield и некоторых других;
- `Nullable(DateTime('UTC'))` для `start_time`, `recv_time`, `original_time`;
- `Nullable(UUID)` для asset ids;
- обязательный `timestamp DateTime('UTC')` и `uuid UUID` в базовой таблице.

`pipeline/clonning/_main.conf` содержит явный `mutations` map. Новое поле не попадёт в ClickHouse только потому, что оно создано в VRL: оно должно быть и в migrator, и в mutations.

Для генератора:

```text
semantic target: event_src.title
VRL/YAML/ClickHouse: event_src_title
```

В target profile должны быть два связанных, но разных описания: семантическая таксономия MP SIEM/Logicore и физическая схема хранения из migrator. Технические поля Logicore выделяем отдельно, чтобы агент не придумывал для них ложную семантику MP SIEM.

## 7. Жизненный цикл входящего Syslog

### 7.1 `input_syslog`

Текущий `syslog_parallel`:

- слушает TCP и UDP на `5140`;
- `parse_syslog=true` по умолчанию;
- текущий custom `grok_pattern` — `^<(?<priority>\d+)>`;
- `use_labels=false`;
- `try_encodings = Windows-1251, KOI8-R, UTF-16BE, UTF-16LE`;
- `proxy_protocol` управляется через `SATURN_INPUT_PROXY_PROTOCOL`;
- добавляет `mime=TEXT`.

Поля:

- `host` = IP отправителя socket; при Proxy Protocol v1 — `clientip` из header;
- `@timestamp` создаётся Logstash при приёме, затем date filter может заменить его временем из Syslog;
- `priority`, `severity`, `facility` вычисляются из PRI; при unknown message PRI=13;
- `recv_ipv4` заполняется в filter из `host`, только если поле отсутствует;
- `recv_ipv6` в текущем input не создаётся, но `pass_fields` normalizer поддерживает его, если оно появилось раньше;
- `message` сохраняется. Отдельная проверка текущего Grok подтвердила: pattern извлекает `priority`, но не обрезает исходную строку;
- управляющие `\x1c..\x1f` заменяются пробелом.

Для auditd есть отдельная ветка `events_aggregate`: события собираются, затем `mime=JSON`, `product=auditd`, `message=JSON.generate(body)`. В TEXT-only scope она не относится.

### 7.2 `parse_autonormalizer`

- Основной `mp_normalizer` читает `mpsiem_tags/siem_filter/norm_rules_siem.yml`.
- `tag_on_failure=normalizer_failure`.
- `tag_on_unparsed=unparsed_autonormalizer`.
- В native передаются `recv_ipv4`/`recv_ipv6` через `pass_fields`.
- Все `.conf` каталога склеиваются в алфавитном порядке, `_main.conf` идёт раньше source-specific файлов.
- Далее событие идёт в `client_settings`.

### 7.3 `client_settings`

Выполняет общую постобработку, которую генератор не должен дублировать:

1. `normalized`, если `id` существует и не пустой; иначе `unparsed`.
2. `generator = ${INSTANCE_ID:logiq-collector}`, если поле отсутствует.
3. `recv_time = @timestamp`.
4. Если `time` отсутствует/пустое: `time = @timestamp`.
5. Иначе `time` разбирается Logstash date filter с `timezone=Europe/Moscow` и списком ISO/custom formats.
6. Если `time - recv_time < -24h` или `> +3h`:
   - `original_time = time`;
   - `time = recv_time`;
   - tag `timestamp_modified`.
7. `uuid` через стандартный Logstash `uuid` filter.
8. Пользовательский `client_settings.conf` с custom routing logic.
9. `syslog_destination` и tag `conveer-N` по hash от `event_src_hostname`, иначе `recv_ipv4`.

Важный нюанс: поскольку входной date filter может записать Syslog timestamp в `@timestamp`, `recv_time` не всегда является чистым временем сетевого приёма. Он наследует уже изменённый входом `@timestamp`.

Собственный plugin `uuid_v` существует, но в текущем `client_settings` используется обычный `uuid`, а не `uuid_v`.

### 7.4 Дальше

- `unparsed` не идёт в `mpsiem_tags`, а сразу в `clonning`.
- `mpsiem_tags`/`mp_filtrator` ставит `not_drop` или `skip`, а также `correlation_rule`.
- `clonning` пишет все события в ClickHouse.
- `output_siem` отправляет только события с `not_drop` и без `drop_mp_siem`.
- ClickHouse `timestamp` заполняется из `time`; `uuid` — из `uuid`.

### 7.5 Что должно создавать правило, а что создаёт Saturn

| Поле/данные | Источник |
|---|---|
| `id`, `time`, `action`, `subject`, `object`, `status`, `importance`, taxonomy fields | Правило агента обязано задать/подтвердить |
| `message` | Исходное Logstash event сохраняется; правило не дублирует |
| `host`, `recv_ipv4`, возможный `recv_ipv6` | Input / pass-through |
| `recv_time` | `client_settings` |
| `generator` | `client_settings` |
| `uuid` | `client_settings` |
| `normalized` / `unparsed` | `client_settings` по наличию `id` |
| `original_time`, `timestamp_modified` | `client_settings` при большом расхождении времени |
| `not_drop`, `skip`, `correlation_rule` | Фильтрация/custom routing, не normalization rule |
| `syslog_destination`, `conveer-N` | `client_settings.conf` |

## 8. AxelNAC: подтверждённая проблема маршрутизации

MR 9 добавляет:

```text
pipeline/parse/axelnac/norm_rules_axelnac.yml
pipeline/parse/autonormalizer/axelnac.conf
```

CONF запускает отдельный `mp_normalizer` только при `unparsed_autonormalizer`, имеет свои `axelnac_normalizer_failure` и `unparsed_axelnac`, а после успеха снимает исходный `unparsed_autonormalizer`.

В текущем `master` базовый `norm_rules_siem.yml` занимает всего 1380 байт и содержит два generic-правила:

- `Default/Syslog-3164`;
- `Default/Syslog-5424`.

Это отличается от приложенного большого production-файла на 21 MB с 9284 правилами. Поэтому поведение нужно проверять на конкретной ветке, а не смешивать версии.

Тестовая строка:

```text
<34>Oct 27 2024 12:00:00 nas01 auth[123]: (456) Login OK: [alice] (from client nas01 port 1 cli aa:bb:cc:dd:ee:ff)
```

Нативный прогон через `libmpsiem.so` дал:

- базовые правила: `id=syslog-3164`;
- AxelNAC rules: `id=PT_Axel_AxelNAC_syslog_login_ok`, ожидаемые taxonomy fields и `numfield1=1`, MAC приведён к uppercase.

**Вывод:** AxelNAC YAML работает, но generic-правило выигрывает раньше. Fallback после generic не получит такое событие. MR сам по себе не доказывает production-маршрутизацию. Для генератора обязателен end-to-end test на попадание именно в source-specific normalizer; при необходимости CONF должен запускаться раньше generic или иметь другую маршрутизацию.

## 9. `saturn-tools`: рабочий контур отладки

Compose монтирует:

```text
docker/saturn/config.yml                  → /config.yml
docker/saturn/entrypoint.sh               → /bin/entrypoint.sh
docker/saturn-pipelines/config            → /usr/share/logstash/config
docker/saturn-pipelines/pipeline          → /usr/share/logstash/conf.d
docker/files                              → /usr/share/logstash/files
```

Также запускаются ClickHouse, RabbitMQ и `saturn-migrator`. Образ зафиксирован на `saturn:v1.1.7`; `TZ=Europe/Moscow`.

`docker/saturn-pipelines` является submodule с pinned commit `145b1270098256c53a3906968877f1a04cd5be27`, но для исследуемой ветки/MR его нужно подменить.

### Подмена pipelines

Рабочий вариант в текущем Windows-окружении:

1. Подготовить отдельный checkout нужной ветки/MR.
2. Скопировать его **как обычные файлы** в `docker/saturn-pipelines`.
3. Не клонировать вложенный репозиторий прямо в этот path: в проверенном случае bind mount видел только пустые директории. Файловая копия устранила проблему.
4. Привести `docker/saturn/entrypoint.sh` из CRLF в LF. Иначе она завершается `exec /bin/entrypoint.sh: no such file or directory`.

После подмены `docker compose config` подтверждает правильные mounts.

### Config test

Текущий `entrypoint.sh` заканчивается:

```bash
exec /usr/local/bin/docker-entrypoint
```

и не пробрасывает `"$@"`. Поэтому `--config.test_and_exit` при обычном запуске игнорируется и стартует обычный server. Для config test нужно напрямую вызвать stock entrypoint:

```bash
docker compose -f docker-compose.yml run --rm --no-deps \
  --entrypoint /usr/local/bin/docker-entrypoint \
  saturn /usr/share/logstash/bin/logstash --config.test_and_exit
```

### RAW и expected

- `analys/send-logs.py` отправляет RAW строки по TCP/UDP, сохраняя bytes через latin-1.
- `analys/test-parsing-mpsiem.py` принимает MP SIEM JSON events, формирует `message/mime/product`, отправляет их и сравнивает ожидаемые поля с ClickHouse по `uuid`.
- Для простых source tests можно отправлять RAW через `send-logs.py` и сравнивать результат собственным fixture-checker.
- `make saturn-logs`, `make analys`, `make analys-graph` помогают смотреть загрузку правил и статистику.
- Полный compose стенд во время обзора не поднимался, чтобы не затронуть уже работающий `clickhouse` container.

### Быстрый native test без полного стенда

В образе есть `/gems/mpsiem/lib/libmpsiem.so`. Можно вызвать Ruby `Mpsiem::Normalizer` напрямую с выбранным YAML и получить normalized event. Это подходит для быстрых checks, но production FFI остаётся не strict, поэтому отдельный strict-loader всё равно необходим.

## 10. Итоговый контракт генератора

### 10.1 Вход

- Связанные parser/normalization/mapping revisions (`Family Bundle`).
- `MpTextTargetProfile`:
  - mpsiem commit/runtime image;
  - разрешённые TEXT tokens;
  - VRL/MP functions и их signatures;
  - семантическая таксономия MP SIEM, её подмножество и технические поля Logicore;
  - ClickHouse/migrator storage schema и типы полей;
  - source name, paths, rule naming и ID policy;
  - CONF tags, `pass_fields`, placement относительно generic normalizer;
  - time policy и доказанные timestamp formats.

### 10.2 Выход

```text
pipeline/parse/<source>/norm_rules_<source>.yml
pipeline/parse/autonormalizer/<source>.conf
RAW positive/negative fixtures
expected normalized events
compilation и validation report
```

### 10.3 Правила компиляции

- Parser agent работает сразу в MP TEXT DSL либо возвращает явный `unsupported`.
- Internal captures — `$variables`; итоговые targets — flat names.
- `id` и `time` обязательны для production-совместимости.
- `message`, `uuid`, `recv_time`, `generator` и routing tags правило не создаёт.
- Normalization и mapping опускаются в VRL `body` детерминированно.
- `value_map` сохраняет выбранную strict-политику; неизвестное значение не должно молча исчезать в `absent=null`.
- UUID правила стабилен между exports и привязан к `rule_key`.
- Пересекающиеся правила требуют явных разграничивающих conditions; порядок YAML не считается гарантией.
- Все неподдерживаемые операции возвращаются в Feedback Registry с точным путём в IR.

### 10.4 Обязательные gates

1. Существующие parser/normalization/mapping evidence checks.
2. Capability validation против конкретного runtime profile.
3. Strict YAML load: все rules/conditions/bodies/appendix компилируются, ноль skipped rules.
4. Native positive/negative execution с проверкой flat fields, types, `id`, `time`, tags.
5. Полный corpus regression и проверка прежних baseline cases.
6. Logstash config test на подменённом `saturn-pipelines`.
7. End-to-end маршрут: событие действительно дошло до source-specific normalizer, а не осталось в generic.
8. ClickHouse comparison: ожидаемые поля, `time/timestamp`, `uuid`, `message` и отсутствие failure/unparsed.
9. Проверка, что generic и другие source parsers не изменили своё поведение.

## 11. Минимальный следующий этап реализации

1. Генератор `MpTextTargetProfile` из исходников конкретной версии: tokens, functions, field types.
2. Strict native validator для произвольного YAML.
3. Parser prompt/tools для TEXT-only MP DSL.
4. Детерминированный compiler `Family Bundle → YAML + CONF`.
5. Исполняемые fixtures и runner через `saturn-tools`.
6. Пилот на небольшом источнике с обязательной проверкой generic-shadowing.

JSON/TABULAR/EVENTLOG, универсальное понижение произвольного regex и автоматическая поддержка всех pipeline codecs в этот этап не входят.

## 12. Проверки и их статус

- Native `libmpsiem.so`: базовые и AxelNAC правила успешно исполнены на тестовой строке.
- Logstash Grok с текущим `grok_pattern`: исходный `message` сохраняется, `priority` извлекается.
- `mpsiem` Cargo tests на Windows: не собраны из-за выбора GNU `link.exe` вместо MSVC linker. Это окружение, не failure тестов.
- `mpsiem-xp-converter` tests на Windows: collection остановлен из-за отсутствия `_curses` в Python 3.13. Это окружение, не failure assertions.
- `mpsiem-xp-converter` в Linux Python 3.11: 2104 tests passed, но полный прогон не зелёный: 2 fixture tests упёрлись в read-only mount, 26 correlation cases — в `Cannot allocate memory` при параллельном Logstash с большой JVM heap. Повторный scoped запуск был прерван ошибкой Docker API 500.
- Полный Logstash startup с подменённым AxelNAC pipeline дошёл до инициализации всех восьми pipelines, включая `parse_autonormalizer` с `_main.conf` и `axelnac.conf`. Отдельный `config.test_and_exit` не доведён до завершения: сначала выяснилось, что custom entrypoint не пробрасывает аргументы, затем повторный запуск был остановлен для ограничения памяти, а финальная попытка прервана ошибкой Docker API 500. Полный config test остаётся обязательным перед MR.

## 13. Реализация production target

Реализован отдельный путь `RAW corpus → MpTextExportSpec → YAML + CONF + fixtures + validation report`; исследовательский Grok/pipeline IR в MP TEXT автоматически не транслируется.

- `mp_profile.py` и `profiles/mp_text_profile_v1_1_7.json`: версионированный target profile, сгенерированный из исходников `mpsiem`, VRL и Saturn migrations. Содержит 28 TEXT tokens, 84 MP functions, 196 VRL functions, 265 storage fields, MP SIEM semantics и Logicore technical fields.
- `native/mp_rule_validator.rs`: strict loader `from_yaml(..., true)`, counts declared/loaded/skipped rules, positive/negative fixtures и JSON report.
- `native/Dockerfile` и `mp_native.py`: reusable image на hash от mpsiem revision, validator source и Cargo.lock. После одной сборки проверка не вызывает Cargo и не скачивает dependencies.
- `mp_export.py` и `mp_schemas.py`: детерминированный compiler; только temporary `$captures`, flat targets, stable UUID от `rule_key`, strict value_map, обязательная таксономия и CONF с приоритетом source-specific success над generic.
- `mp_agent.py`, `mp_tools.py`, `mp_prompts.py`: реальный сценарий unknown logs → candidate → native evaluation → export → full corpus gate. LLM не возвращает YAML напрямую.
- `mp_saturn_test.py`: файловая подмена pipelines без копирования `.git`, восстановление после теста, изолированный compose project `mp-saturn-agent` с отдельными containers/volume/ports. Штатный стек ClickHouse + RabbitMQ + migrator + Saturn; config test выполняется с `source /bin/init_config.sh`.
- `output/axelnac/pipeline/parse/axelnac/norm_rules_axelnac.yml` и `output/axelnac/pipeline/parse/autonormalizer/axelnac.conf`: pilot с login_ok, TLS, login_incorrect и no-CLI.

Подтверждено после реализации:

- Python suite: 40 tests passed.
- Original AxelNAC YAML: 20/20 rules strict loaded, 3 positive + 2 negative passed.
- Generated pilot: 4/4 rules strict loaded, 4 positive + 2 negative passed, skipped=0.
- Production FFI через `libmpsiem.so` выявил практическую пользу strict loader: fallible `strip_whitespace` привёл к молчаливому пропуску одного generated rule обычным loader. Compiler исправлен на `strip_whitespace!`; повторный strict/native прогон успешен.
- Reusable validator image сохраняется между запусками; повторный export занимает секунды и не выполняет Rust build.
- Saturn E2E: `output/axelnac/fixtures/axelnac/saturn_e2e_report.json` — PASS, 4/4 RAW события записаны в ClickHouse с ожидаемыми ID и полями. Проверены login_ok, TLS, incorrect с CLI и без CLI. Отдельный повторный `config.test_and_exit` убран из E2E: сам Saturn успешно поднял все pipelines, а второй процесс зависал на 300 секунд.
- Во время E2E обнаружен и исправлен реальный routing defect: сначала `login_incorrect` оставался `syslog-3164`, потому что `dispatch_contains` содержал только `Login OK:`. После добавления `Login incorrect` получен `PT_Axel_AxelNAC_syslog_login_incorrect`. В compiler добавлен gate: каждая positive RAW обязана покрываться dispatch anchors.
- Checker учитывает сохранённый Saturn newline от TCP framing и `output_format_json_quote_64bit_integers=0` для сравнения Int64.
- Реальный LLM сценарий выполнял research/preview/native evaluations, но два запуска завершились внешней `OpenAIConnectionError`; agent-generated пакет не считается подтверждённым. Детерминированный candidate/export пилота при этом прошёл все native и Saturn gates.

## 14. Переход от monolithic MP agent к расширению исходного parser-агента

Первый вариант `mp_agent.py` оказался отдельным production-specific агентом и не сохранял основной parser → normalization → mapping workflow. Это не принято как целевая архитектура. Production backend остаётся, а монолитная оркестрация заменяется на target mode существующего агента.

Реализовано:

- `native/mp_rule_validator.rs` получил команду `captures PATTERNS.json`: `GrokText.search` возвращает captures после postcapture, но до VRL/`normalize_value` и удаления `$variables`; отчёт содержит matched rule ids и ambiguity.
- `ParserDefinition.kind` расширен `mp_text`; добавлены `text_pattern` и `negative_evidence`. Grok/pipeline modes не изменены.
- `parser_families` расширены `target`, `text_pattern`, `negative_evidence_json`; старые БД получают defaults через ALTER. MP TEXT сохраняется тем же `storage.save_family`.
- `runtime.execute_family` выбирает native MP TEXT по `family.target`; research Grok/pipeline продолжают прежнее исполнение.
- В `tools.py` добавлены `get_mp_text_capabilities`, `execute_mp_text_parser`, `evaluate_mp_text_parser`, `save_mp_text_family`.
- `agent.py` и batch mode принимают `target=mp_text`; добавляются только target tools/prompt, Response и runner остаются общими.
- `corpus.py` и `batch_corpus.py` фильтруют dispatch по target, чтобы research и MP TEXT Families не смешивались.
- `validation.py` и `storage.get_parser_research` проверяют MP TEXT persistence и evidence через тот же Response/Family контракт.

Parser-only AxelNAC pilot: 4 Families (`login_ok`, TLS, incorrect, no-CLI) сохранены в `parser_families`; native expected captures, negatives и target dispatch прошли. Полный Python suite после расширения: 47 tests passed.

Реальный исходный `agent.py --target mp_text` на новой БД успешно создал Family `syslog_auth_login_ok_nas_client@1`: полный research Response, record_schema, evidence, 5 negative cases, native evaluation и persistence прошли. Это подтверждает, что MP TEXT является расширением существующего parser-агента, а не отдельным monolithic workflow.

## 15. Финальный registry-generated production пакет

Завершён полный исходный workflow:

```text
syslog_auth_login_ok_nas_client parser@1
  → normalization@1 через normalization_agent.py
  → mapping@1 и исправленная mapping@2 через mapping_agent.py
  → mp_export.py spec_from_revisions
  → YAML + CONF + fixtures
```

`mp_export.py` получил `spec_from_revisions`: загрузка связанных revisions, повторная mapping evaluation, expected events, native DATETIME projection, dispatch anchors и CLI `--family-id/--mapping-revision/--source/--database`.

Финальные файлы:

- `output/registry-axelnac/pipeline/parse/axelnac/norm_rules_axelnac.yml`;
- `output/registry-axelnac/pipeline/parse/autonormalizer/axelnac.conf`;
- `output/registry-axelnac/fixtures/axelnac/validation_report.json`;
- `output/registry-axelnac/fixtures/axelnac/saturn_e2e_report.json`.

Проверки:

- strict native: 1/1 rule loaded, skipped=0, 1 positive + 5 negative passed;
- итоговый `id=PT_unknown_syslog_syslog_login`; `unknown/syslog` выбраны mapping-агентом из-за отсутствия внешнего подтверждения производителя, а не заменены вручную;
- Saturn RAW→ClickHouse: PASS, ожидаемые ID/taxonomy/numbers/time совпали;
- полный Python suite: 50 tests passed.

Timestamp gate разделяет native и Saturn states: DATETIME capture сохраняет wall-clock `2024-10-27T12:00:00.000Z`, а Saturn `client_settings` формирует ожидаемое `2024-10-27T09:00:00.000Z`. При `timestamp_modified` checker сравнивает `original_time` и учитывает ISO precision. Runner получил fresh isolated volume и readiness всех восьми Logstash pipelines перед отправкой RAW.
