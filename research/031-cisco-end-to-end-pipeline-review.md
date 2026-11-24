# R031 — от пустой системы к MP SIEM event: одна Cisco-строка через три агента и Rust

> Подробный сквозной разбор того, как RAW превращается в результат Rust. Рассматриваются холодный старт с пустым SQLite Registry, обучение parser/normalizer/mapper, экспорт Family Bundle, загрузка Rust и повторная обработка той же строки уже без модели.

## 1. Исходная строка и конечная цель

Вход — одна неизменённая строка:

```text
<132>Feb 12 2023 13:00:30 10.11.200.67 : %ASA-4-106100: access-list CSM_FW_ACL_ denied tcp Outside/131.9.85.43(14015) -> PUB_DMZ/191.189.210.52(22) hit-cnt 1 first hit [0x6921d058, 0x00000000]
```

На выходе Rust должен вернуть:

- выбранную Family и revisions;
- строковые captures;
- normalized values с преобразованными типами;
- плоское MP SIEM event;
- `ok=true`, `status=mapped`, пустой список ошибок.

Фактически используемый Bundle содержит:

```text
Family:                 cisco_asa_acl_verdict
Parser revision:        4
Normalization revision: 2
Mapping revision:       4
Taxonomy:               mp_siem_8.0
```

### Важная оговорка про «пустую систему»

Эта конкретная строка исторически создала `cisco_asa_acl_verdict` на parser revision 2. Revisions 3 и 4 появились после дополнительных наблюдений: сначала host в форме `устройство/интерфейс`, затем отсутствующий host. Поэтому:

- **логика холодного запуска** ниже описывается с первой RAW-строки;
- **промежуточные значения и финальный JSON** взяты из фактически проверенного Bundle `4/2/4`;
- при совершенно новом запуске с одной только этой строкой revisions начались бы с ранних номеров, а parser мог быть уже текущего rev4: дополнительные варианты host ещё не были бы доказаны;
- для точного воспроизведения текущего Bundle нужны его утверждённые revisions либо повторение evidence, на которых они возникли.

Это не влияет на результат разбора данной строки: rev4 полностью принимает её.

## 2. Вся система одной схемой

```text
ХОЛОДНЫЙ СТАРТ / CONTROL PLANE / PYTHON

Пустой SQLite Registry
  │
  ▼
Parser ReAct-агент
  • ищет существующие Families и Grok — пусто
  • понимает событие и строит record_schema
  • создаёт Grok-конструкции и два слоя
  • исполняет и проверяет parser
  • сохраняет Grok, Family и research Response
  │
  ▼
Normalization ReAct-агент
  • получает Family и исходный parser Response
  • описывает смысл каждого выходного поля
  • задаёт минимальные operations
  • проверяет preview и все evidence
  • сохраняет normalization revision
  │
  ▼
Mapper ReAct-агент
  • получает guidelines, parser Response и normalization
  • семантически ищет поля/значения/категории MP SIEM
  • проектирует mappings и константы
  • проверяет preview и все evidence
  • сохраняет mapping revision
  │
  ▼
bundle.py
  • раскрывает mapping → normalization → parser
  • добавляет taxonomy и dispatch anchors
  • считает compatibility hash
  • сохраняет Family Bundle JSON


РАБОЧИЙ ЦИКЛ / DATA PLANE / RUST

Family Bundle JSON
  │
  ▼ загрузка один раз
Проверка hash/schema/revisions
  → компиляция Grok regex
  → построение Aho–Corasick automaton
  │
  ▼ каждая новая RAW-строка
Aho–Corasick candidate shortlist
  → полный parser только кандидатов
  → captures + schema/constraints validation
  → normalization
  → mapping
  → taxonomy validation
  → итоговый ProcessResult JSON
```

Важно: сейчас это три последовательных эксплуатационных шага, а не один cold-start CLI:

1. `corpus.py` обучает и сохраняет revisions;
2. `bundle.py` экспортирует выбранные revisions;
3. `log-data-plane` исполняет Bundle.

Rust сам не вызывает агентов. Если ни один Bundle не подходит, он возвращает `no_family`; обучение новой Family остаётся задачей control plane.

## 3. Шаг 1. Пустой Registry и первый dispatch в Python

При подключении `storage.db_connect()` создаёт таблицы, среди которых:

- `grok_patterns`;
- `parser_families`;
- `research_results`;
- `normalization_revisions`;
- `mapping_revisions`;
- таблицы таксономии и semantic embeddings.

Воображаемый начальный Registry не содержит пользовательских Grok и Families.

`corpus.py` вызывает `matching_parsers(raw)`:

1. получает latest revisions всех Families;
2. исполняет каждую на RAW;
3. проверяет captures, required-поля, типы, диапазоны, constraints и residual;
4. при отсутствии совпадения проверяет исторические revisions;
5. если совпадений нет — отправляет строку parser-агенту.

На пустой базе список пуст, поэтому LLM вызывается уже на первой строке. На повторной строке тот же механизм нашёл бы сохранённую Family без LLM.

## 4. Шаг 2. Parser ReAct-агент

### 4.1. Вход агента

`run_observation()` формирует независимый пользовательский prompt с RAW между `<log>` и `</log>`. Истории предыдущего диалога нет, но SQLite доступна через инструменты.

System prompt требует финальный JSON `Response`. Модель работает циклом ReAct: вызов инструмента → структурированный результат → следующее действие. Ошибки инструментов и финальной Pydantic-схемы возвращаются модели как исправимые сообщения, а не обрывают исследование.

### 4.2. Сначала поиск, а не создание

По prompt модель обязана вызвать:

```text
semantic_search_parser_families
semantic_search_grok_patterns
```

При необходимости:

```text
list_parser_families
search_grok_patterns
semantic_search_parsers
```

На пустой базе результаты пустые. Только после фактического отсутствия подходящего parser допускается `new_family`.

Исторический Response для этой RAW зафиксировал именно такой вывод: база не содержала ни Family, ни Grok, поэтому переиспользовать или расширять было нечего.

### 4.3. Семантическое понимание события

Модель определяет:

- источник записи — Cisco ASA;
- регистратор — узел `10.11.200.67`;
- сообщение — `%ASA-4-106100`;
- субъект действия — ACE в `CSM_FW_ACL_`;
- действие — `denied`, пакет отброшен;
- взаимодействие — TCP от `131.9.85.43:14015` к `191.189.210.52:22`;
- интерфейсы — `Outside` и `PUB_DMZ`;
- счётчик — первое срабатывание, `hit-cnt 1`;
- два последних hex-значения — технические непрозрачные значения, а не доказанные хеши с конкретным назначением.

Порт 22 может подсказать SSH, но это не доказывает прикладной протокол. Поэтому в итоговом mapping `protocol.layer7=ssh` отсутствует.

### 4.4. Структура и стратегия

Parser-агент разделяет строку на:

```text
$                       вся RAW без изменений
$.pri                   132
$.timestamp             Feb 12 2023 13:00:30
$.host                  10.11.200.67
$.severity              4
$.msg_id                106100
$.body                  access-list ... [0x..., 0x...]
$.body.acl_id           CSM_FW_ACL_
$.body.action           denied
$.body.protocol         tcp
$.body.src_*            левая конечная точка flow
$.body.dst_*            правая конечная точка flow
$.body.hit_cnt          1
$.body.hit_interval     first hit
$.body.packet_hash      0x6921d058
$.body.hit_hash         0x00000000
```

Ключевые проектные решения:

- конверт и payload разбираются отдельно;
- `msg_id=106100` — стабильный dispatch key Family;
- `severity`, `action`, `protocol` — dynamic;
- порядок слева/справа от `->` задаёт source/destination;
- RAW не подвергается предварительному unescape или переписыванию;
- неизвестные hex не получают выдуманной семантики;
- каждый слой должен совпасть со всем своим входом, residual обязан быть пустым.

### 4.5. Grok-конструкции

На пустой базе модель создаёт собственные атомы. В финальном Bundle их 17:

```text
INT
IPV4
SYSLOG_PRI
CISCO_TIMESTAMP
ASA_DEVICE_ID
ASA_ENVELOPE_HOST
ASA_SEVERITY
ASA_MSG_ID
IFACE
PORT
ASA_ACL_ID
ASA_ACL_ACTION
ASA_ACL_PROTOCOL
ASA_HIT_INTERVAL
ASA_HIT_HASH
ASA_ACL_FLOW
ASA_ACL_VERDICT
```

Примеры атомов:

```regex
INT              = [0-9]+
SYSLOG_PRI       = (?:[0-9]{1,2}|1[0-8][0-9]|19[01])
ASA_SEVERITY     = [0-7]
ASA_MSG_ID       = [0-9]{6}
ASA_ACL_ACTION   = (?:denied|permitted)
ASA_ACL_PROTOCOL = (?:tcp|udp|sctp)
ASA_HIT_INTERVAL = (?:first hit|[0-9]+-second interval)
ASA_HIT_HASH     = 0x[0-9A-Fa-f]+
```

`PORT` ограничен диапазоном 0..65535, `IPV4` проверяет октеты, `CISCO_TIMESTAMP` требует год. Имя устройства отличается от IPv4 при помощи lookahead: в `ASA_DEVICE_ID` должна быть хотя бы одна буква.

Для новых конструкций модель задаёт positive и negative examples. Они исполняются независимо: красивое описание без работающего regex не проходит quality gate.

### 4.6. Два parser-слоя

Финальный rev4 содержит:

```text
Layer 1: syslog_envelope
Source:  raw
Pattern: <%{SYSLOG_PRI:pri}>%{CISCO_TIMESTAMP:timestamp}(?: %{ASA_ENVELOPE_HOST:host} )?: %ASA-%{ASA_SEVERITY:severity}-%{ASA_MSG_ID:msg_id}: %{ASA_ACL_VERDICT:body}

Layer 2: acl_payload
Source:  body
Pattern: %{ASA_ACL_VERDICT:verdict}
```

Первый слой одновременно:

- извлекает envelope-поля;
- находит `%ASA-4-106100`;
- захватывает всё тело в `body`;
- требует, чтобы тело соответствовало `ASA_ACL_VERDICT`.

Второй слой повторно разбирает `body` как самостоятельный вход и создаёт `verdict`. Поэтому в captures есть и промежуточные `body`, `flow`, `verdict`, и атомарные поля.

Составной `ASA_ACL_VERDICT`:

```text
access-list %{ASA_ACL_ID:acl_id}
%{ASA_ACL_ACTION:action}
%{ASA_ACL_PROTOCOL:protocol}
%{ASA_ACL_FLOW:flow}
hit-cnt %{INT:hit_cnt}
%{ASA_HIT_INTERVAL:hit_interval}
\[%{ASA_HIT_HASH:packet_hash}, %{ASA_HIT_HASH:hit_hash}\]
```

Здесь переносы показаны для читаемости; фактический pattern — одна строка с одиночными пробелами-литералами.

### 4.7. Исполнение и quality gates до сохранения

Модель обязана:

1. исполнить parser через `execute_grok_parser`;
2. проверить `validate_grok_result`;
3. вызвать `evaluate_candidate`;
4. для новой Family проверить новую строку и независимый baseline, если он есть;
5. сохранить Grok через `save_grok_pattern`;
6. сохранить Family через `save_parser_family` с двумя развёрнутыми описаниями;
7. перечитать их через `get_grok_pattern` и `get_parser_family`.

Runner затем валидирует полный `Response` и сохраняет его в `research_results`. Этот Response позже получает нормализатор через `get_parser_research`: семантика не восстанавливается заново только по regex.

### 4.8. Почему текущий parser — rev4

Фактическая эволюция:

| Revision | Новое наблюдение | Изменение |
|---|---|---|
| 2 | Текущая RAW с IPv4 host | Новая Family, 15 Grok |
| 3 | `J0061fw01-P/Internet` | Host расширен до IPv4, hostname и `hostname/interface`; 17 Grok |
| 4 | Host отсутствует | Host-сегмент стал необязательной группой, `host.required=false` |

Во всех случаях тело 106100 сохранилось. Это `extend_layer`, а не создание новой Family для каждой мелкой вариации.

## 5. Шаг 3. Parser результат на нашей строке

После двух слоёв получаются 21 string capture:

| Capture | Значение |
|---|---|
| `pri` | `132` |
| `timestamp` | `Feb 12 2023 13:00:30` |
| `host` | `10.11.200.67` |
| `severity` | `4` |
| `msg_id` | `106100` |
| `body` | Полное тело после `%ASA-4-106100: ` |
| `acl_id` | `CSM_FW_ACL_` |
| `action` | `denied` |
| `protocol` | `tcp` |
| `flow` | `Outside/131.9.85.43(14015) -> PUB_DMZ/191.189.210.52(22)` |
| `src_iface` | `Outside` |
| `src_ip` | `131.9.85.43` |
| `src_port` | `14015` |
| `dst_iface` | `PUB_DMZ` |
| `dst_ip` | `191.189.210.52` |
| `dst_port` | `22` |
| `hit_cnt` | `1` |
| `hit_interval` | `first hit` |
| `packet_hash` | `0x6921d058` |
| `hit_hash` | `0x00000000` |
| `verdict` | Полное тело 106100 |

`record_schema` также декларирует `raw`, но validator специально пропускает `raw` и `residual` при проверке captures. Поэтому отсутствие `raw` среди 21 captures не является ошибкой.

Проверяются:

- required captures присутствуют и непусты;
- integer-поля читаются как числа;
- `pri=132` входит в 0..191;
- порты входят в 0..65535;
- IP-поля являются IP;
- constraints выполняются — в этой revision список пуст;
- residual пуст.

На этом этапе никаких MP SIEM-имён ещё нет.

## 6. Шаг 4. Normalization ReAct-агент

### 6.1. Его задача и граница

Normalizer не меняет parser, не переименовывает поля и не выбирает MP SIEM targets. Он:

- сохраняет исходные captures;
- описывает смысл каждого output-поля;
- преобразует значения минимальными operations;
- сохраняет результат под исходными именами.

Поле `target` отсутствует намеренно. Развёрнутые descriptions важнее имен, потому что mapper ищет соответствия по смыслу.

### 6.2. Входные данные

Агент вызывает:

```text
get_parser_family
get_parser_research
```

Он получает не только captures, но и исходные `essence`, `structure`, `strategy`, `format_features`, `pitfalls` parser-агента. Затем изучает record_schema и evidence.

Для каждого record_schema-поля, кроме `raw`, создаётся один `NormalizationField`. В текущей revision их 18.

### 6.3. Operations на нашей строке

| Поле | До | Operations | После |
|---|---|---|---|
| `pri` | `"132"` | `to_integer`, `validate_range(0..191)` | `132` |
| `timestamp` | `"Feb 12 2023 13:00:30"` | `parse_timestamp("%b %d %Y %H:%M:%S")` | `"2023-02-12T13:00:30"` |
| `severity` | `"4"` | `to_integer`, `validate_range(0..7)` | `4` |
| `src_ip` | `"131.9.85.43"` | `normalize_ip(ipv4)` | `"131.9.85.43"` |
| `src_port` | `"14015"` | `to_integer`, `validate_range(0..65535)` | `14015` |
| `dst_ip` | `"191.189.210.52"` | `normalize_ip(ipv4)` | `"191.189.210.52"` |
| `dst_port` | `"22"` | `to_integer`, `validate_range(0..65535)` | `22` |
| `hit_cnt` | `"1"` | `to_integer` | `1` |

Остальные described fields сохраняют значение:

```text
host, msg_id, acl_id, action, protocol,
src_iface, dst_iface, hit_interval, packet_hash, hit_hash
```

Промежуточные `body`, `flow`, `verdict` не входят в 18 NormalizationField, но executor сначала копирует все captures, поэтому они тоже остаются в normalized dictionary без изменений.

### 6.4. Почему timestamp пока без `Z`

RAW не содержит timezone. Нормализатор не имеет права выдумывать UTC, поэтому возвращает naive ISO:

```text
2023-02-12T13:00:30
```

А не:

```text
2023-02-12T13:00:30Z
```

`Z` появится только в mapping, потому что для исследовательского runner извне задано `source_timezone=UTC`. Это лабораторная настройка, а не вывод из Cisco-строки.

### 6.5. Проверка и сохранение

Агент вызывает:

```text
preview_normalization       на реальной строке
evaluate_normalization      на всех parser evidence
save_normalization          с точным parser_revision
get_normalization           для проверки persisted content
```

Runner независимо повторяет evaluation и проверяет, что финальный `NormalizationRevision` совпадает с сохранённым. В R027 эта revision нормализовала 30/30 относящихся записей без изменения parser regression 1763/1763.

## 7. Шаг 5. Mapper ReAct-агент и Taxonomy Registry

### 7.1. Подготовка таксономии на пустой системе

Пустой learning Registry не означает отсутствие справочника. Статические файлы `reference/mpsiem` загружаются в SQLite:

- 212 taxonomy fields;
- 232 enum values;
- 222 event categories.

По текстовым описаниям создаются embeddings. В R028 Registry всего 739 embeddings, из них 666 относятся к таксономии.

Mapper сначала получает `get_mapping_guidelines`, затем:

```text
get_parser_family
get_parser_research
get_normalization
```

Для кандидатов использует:

```text
semantic_search_taxonomy(taxonomy_field)
semantic_search_taxonomy(taxonomy_value)
semantic_search_taxonomy(taxonomy_category)
```

Score только ранжирует. Точный выбор подтверждается через `get_taxonomy_field`, `get_taxonomy_values`, `get_taxonomy_categories` и фактический preview.

### 7.2. Порядок mapping

Список из 29 MappingField следует порядку справочника:

```text
обязательный каркас
 → category.*
 → subject
 → object attributes
 → src/dst
 → event_src
 → остальные параметры взаимодействия
 → datafield/numfield
```

Автоматические поля MP SIEM вручную не заполняются.

### 7.3. Все mappings на нашей строке

| # | Target | Правило | Результат |
|---:|---|---|---|
| 1 | `time` | `timestamp` + внешняя `source_timezone=UTC` | `2023-02-12T13:00:30Z` |
| 2 | `id` | constant | `mp_cisco_asa_syslog_acl_verdict` |
| 3 | `event_src.vendor` | constant | `cisco` |
| 4 | `event_src.title` | constant | `asa` |
| 5 | `event_src.category` | constant | `Firewall` |
| 6 | `action` | `action`: denied→deny, permitted→allow | `deny` |
| 7 | `object` | constant | `connection` |
| 8 | `status` | constant | `success` |
| 9 | `importance` | severity 0..2→high, 3..4→medium, 5..6→low, 7→info | `medium` |
| 10 | `category.generic` | constant | `Accounting` |
| 11 | `category.high` | constant | `Network Accounting` |
| 12 | `category.low` | constant | `Firewall Rules Usage` |
| 13 | `subject` | constant | `rule` |
| 14 | `subject.name` | `acl_id` | `CSM_FW_ACL_` |
| 15 | `src.ip` | `src_ip` | `131.9.85.43` |
| 16 | `src.port` | `src_port` | `14015` |
| 17 | `dst.ip` | `dst_ip` | `191.189.210.52` |
| 18 | `dst.port` | `dst_port` | `22` |
| 19 | `event_src.ip` | `host`, только если `format=ip` | `10.11.200.67` |
| 20 | `event_src.hostname` | `host`, только если `format=hostname` | **Пропущено:** host является IP |
| 21 | `protocol` | `protocol` | `tcp` |
| 22 | `msgid` | `msg_id` | `106100` |
| 23 | `count.packets` | `hit_cnt` | `1` |
| 24 | `interface` | `src_iface` | `Outside` |
| 25 | `datafield1` | `hit_interval` | `first hit` |
| 26 | `datafield2` | `packet_hash` | `0x6921d058` |
| 27 | `datafield3` | `hit_hash` | `0x00000000` |
| 28 | `datafield4` | `dst_iface` | `PUB_DMZ` |
| 29 | `numfield1` | `pri` | `132` |

В итоговом event 28 полей: 29 mappings, но conditional `event_src.hostname` на этой записи не применился.

### 7.4. Почему выбраны неочевидные значения

- `host=10.11.200.67` — это регистратор, поэтому `event_src.ip`, а не `src.ip`.
- `denied` — действие запрета, поэтому `action=deny`.
- Запрет успешно выполнен устройством, поэтому `status=success`; это не утверждение, что соединение установлено.
- `object=connection`, а не `rule`: правило — субъект, соединение — объект воздействия.
- `severity=4` становится `importance=medium`; PRI отдельно сохраняется в `numfield1`.
- Hex-значения не выдаются за подтверждённые packet/object hashes и попадают в `datafield2/3`.
- `direction` и `protocol.layer7` не выдумываются по интерфейсам и порту.

Для `value_map` перечислены все допустимые исходные значения. Default нет: неизвестное значение вызовет ошибку, а не будет скрыто.

### 7.5. Проверка и сохранение

Mapper вызывает:

```text
preview_mapping
evaluate_mapping
save_mapping
get_mapping
```

Runner повторно исполняет parser → normalization → mapping на evidence и проверяет persisted revision. Текущая rev4 прошла 4/4 evidence.

## 8. Шаг 6. Экспорт Family Bundle

`bundle.py export` начинает с mapping revision и раскрывает ссылки:

```text
mapping rev4
  parser_revision=4
  normalization_revision=2
       ↓
normalization rev2
  parser_revision=4
       ↓
parser rev4
```

Если requested revisions противоречат ссылкам, экспорт завершается ошибкой.

В `r030-cisco-acl-verdict.bundle.json` попадают parser, normalization, mapping, taxonomy, descriptions, evidence и dispatch.

### 8.1. Dispatch anchors

Для первого Grok-слоя экспортёр извлекает устойчивые литералы. Отдельно рассматриваются required static-поля record_schema. `msg_id` является static, required и имеет одно значение `106100` во всех evidence.

После фильтрации по evidence получается:

```json
{
  "strategy": "aho_corasick_all_anchors",
  "anchors": ["106100", ": %ASA-"],
  "fallback": false
}
```

Это не самостоятельное доказательство Family: наличие обоих якорей лишь создаёт candidate. Полный parser подтверждает выбор.

### 8.2. Compatibility hash

Python сериализует содержимое без `compatibility_hash` в канонический JSON с сортированными ключами и считает SHA-256.

Фактический hash:

```text
2e8ada129cbb9b140563346ad400bfb96df09850873a55b12f6604b3a7d5f490
```

Golden экспортируется отдельно и содержит Python results для четырёх evidence. Он нужен только для parity tests.

## 9. Шаг 7. Загрузка Rust

### 9.1. Чтение и проверка Bundle

`FamilyBundle::from_path()`:

1. читает JSON;
2. повторно вычисляет SHA-256;
3. десериализует структуры;
4. проверяет `schema_version=1`;
5. сверяет hash;
6. проверяет одинаковый `family_id` во всех частях;
7. проверяет parser/normalization/mapping revision links;
8. проверяет совпадение mapping taxonomy с названием встроенной таксономии.

Изменение даже `source_timezone` без пересчёта hash приводит к `compatibility_hash_mismatch`.

### 9.2. Компиляция Grok

`CompiledParser::new()` получает определения всех 17 Grok. Для каждого слоя `compile_grok()`:

- находит `%{NAME}` и `%{NAME:capture}`;
- рекурсивно подставляет тела конструкций, не более 20 итераций;
- превращает capture в Rust named group:

```text
%{SYSLOG_PRI:pri}
  ↓
(?P<pri>(?:[0-9]{1,2}|1[0-8][0-9]|19[01]))
```

- конструкцию без capture оборачивает в non-capturing group;
- после полного раскрытия добавляет полные якоря:

```regex
\A(?: ...раскрытый pattern... )\z
```

- компилирует результат через `fancy-regex`.

Таким образом, частичное совпадение не засчитывается. Неизвестная конструкция, цикл или ошибка regex останавливают загрузку Bundle, а не обнаруживаются в середине потока.

### 9.3. Построение dispatch automaton

`FamilyRegistry::new()`:

- создаёт `FamilyEngine` для каждого Bundle;
- сортирует по `family_id`, затем parser revision descending;
- собирает объединённый набор anchors;
- строит один `AhoCorasick`;
- сохраняет для каждой Family набор индексов обязательных anchors;
- отдельно хранит fallback Families.

Aho–Corasick внутри библиотеки строит trie с переходами при неуспехе. Это не общий DFA полных Grok-выражений.

Скомпилированные regex и automaton остаются только в памяти. При restart операция повторяется; на каждую RAW повторной компиляции нет.

## 10. Шаг 8. Rust dispatch на нашей RAW

`FamilyRegistry::process(raw)` вызывает `candidates(raw)`.

Aho–Corasick overlapping search находит в строке:

```text
": %ASA-"  перед сигнатурой
"106100"   в message ID
```

Набор найденных anchors содержит оба обязательных индекса, поэтому:

```json
{
  "candidate_count": 1,
  "candidates": ["cisco_asa_acl_verdict@4"]
}
```

Дальше возможны три ситуации:

- нет candidates → `no_family`;
- candidates есть, но полный parser никого не принял → `no_family`;
- полный parser приняли несколько Families → `ambiguous_family`.

У нас полный parser принял ровно одну Family.

## 11. Шаг 9. Rust parsing

`CompiledParser::parse()` выбирает режим layers, потому что Bundle содержит Grok layers и пустой stages.

Для `syslog_envelope`:

- вход — вся RAW;
- полный regex совпадает;
- создаются envelope captures, `body`, а также все captures вложенного `ASA_ACL_VERDICT`.

Для `acl_payload`:

- вход — capture `body`;
- полный regex `%{ASA_ACL_VERDICT:verdict}` совпадает;
- создаётся `verdict`.

Captures объединяются. Затем `validate_captures()` проверяет record schema и constraints. Результат совпадает с таблицей из раздела 5.

Rust не вызывает parser-агента и не принимает решений по смыслу: он только исполняет уже сохранённые правила.

## 12. Шаг 10. Rust normalization

`process_parsed()` сначала формирует `ProcessResult` с revisions `4/2/4`, затем вызывает `normalize_captures()`.

Executor:

1. копирует все 21 captures в `normalized`;
2. последовательно обходит 18 NormalizationField;
3. проверяет required source;
4. применяет operations слева направо;
5. отклоняет неизвестные operation params;
6. проверяет required после operations;
7. сохраняет значение под исходным именем.

Пример `pri`:

```text
"132"
  → to_integer
132
  → validate_range(min=0, max=191)
132
```

Пример `timestamp`:

```text
"Feb 12 2023 13:00:30"
  → NaiveDateTime::parse_from_str(..., "%b %d %Y %H:%M:%S")
  → format("%Y-%m-%dT%H:%M:%S%.f")
"2023-02-12T13:00:30"
```

Ошибка любой required operation дала бы `status=normalization_failed` и остановила mapping.

## 13. Шаг 11. Rust mapping

`apply_mappings()` обходит 29 MappingField в сохранённом порядке.

Для каждого правила:

1. проверяет отсутствие duplicate targets и порядок mapping ranks;
2. берёт constant либо source из normalized;
3. пропускает отсутствующий optional source;
4. проверяет condition;
5. применяет `value_map`, если задан;
6. для `time`/`start_time` вызывает UTC conversion;
7. вставляет плоский target в event.

На нашей строке:

- `action="denied"` найден в value_map и стал `"deny"`;
- `severity=4` превратился в ключ `"4"` и стал `"medium"`;
- `host` прошёл `format=ip` и попал в `event_src.ip`;
- тот же host не прошёл `format=hostname`, поэтому второй mapping пропущен без ошибки;
- naive timestamp получил внешнюю UTC и стал `2023-02-12T13:00:30Z`.

Если бы `source_timezone` не был задан, `time` вернул бы `naive_datetime_requires_source_timezone`. Нормализация при этом уже была бы успешной.

## 14. Шаг 12. Rust taxonomy validation

Перед успешным завершением `validate_event()` использует таксономию из Bundle, а не SQLite:

- присутствуют все required fields;
- отсутствуют unknown taxonomy fields;
- автоматические fields не заполнены;
- Number содержит integer;
- IPAddress валиден;
- DateTime имеет UTC offset;
- Enum значения входят в справочник;
- String действительно string;
- vendor/title в нижнем регистре и без пробелов;
- id содержит минимум пять namespace-компонентов и не содержит пробелов;
- hostname не является IP, не содержит `/` или whitespace;
- `category.generic/high/low` образуют одну допустимую ветвь.

Наши категории образуют существующую ветвь:

```text
Accounting → Network Accounting → Firewall Rules Usage
```

Ошибок нет, поэтому:

```text
ok=true
status=mapped
errors=[]
```

## 15. Финальный MP SIEM event из Rust

```json
{
  "time": "2023-02-12T13:00:30Z",
  "id": "mp_cisco_asa_syslog_acl_verdict",
  "event_src.vendor": "cisco",
  "event_src.title": "asa",
  "event_src.category": "Firewall",
  "action": "deny",
  "object": "connection",
  "status": "success",
  "importance": "medium",
  "category.generic": "Accounting",
  "category.high": "Network Accounting",
  "category.low": "Firewall Rules Usage",
  "subject": "rule",
  "subject.name": "CSM_FW_ACL_",
  "src.ip": "131.9.85.43",
  "src.port": 14015,
  "dst.ip": "191.189.210.52",
  "dst.port": 22,
  "event_src.ip": "10.11.200.67",
  "protocol": "tcp",
  "msgid": "106100",
  "count.packets": 1,
  "interface": "Outside",
  "datafield1": "first hit",
  "datafield2": "0x6921d058",
  "datafield3": "0x00000000",
  "datafield4": "PUB_DMZ",
  "numfield1": 132
}
```

Это плоский объект: `event_src.vendor` — буквальное имя ключа, а не вложенный `event_src`.

Внешний `ProcessResult` дополнительно содержит:

```json
{
  "ok": true,
  "status": "mapped",
  "family_id": "cisco_asa_acl_verdict",
  "parser_revision": 4,
  "normalization_revision": 2,
  "mapping_revision": 4,
  "captures": {"...": "21 string captures"},
  "normalized": {"...": "21 values с преобразованными типами"},
  "event": {"...": "MP SIEM event из 28 полей"},
  "errors": [],
  "candidates": ["cisco_asa_acl_verdict@4"]
}
```

Сокращения `"..."` показаны только здесь; реальный CLI возвращает полные dictionaries.

## 16. Что произойдёт на следующей такой же строке

После холодного обучения и экспорта модель больше не нужна:

```text
Новая RAW
 → уже загруженный Aho–Corasick
 → уже скомпилированный parser
 → уже загруженные operations/mappings/taxonomy
 → новый ProcessResult
```

Изменяются только значения captures/normalized/event. Bundle и SQLite не перезаписываются.

Если придёт другой ASA message ID:

- `106100` не найдётся;
- текущая Family не попадёт в shortlist;
- при отсутствии других Bundles Rust вернёт `no_family`;
- control plane может затем исследовать строку, создать новую revision/Family и выпустить новый Bundle.

Если придёт `106100` с новой грамматикой:

- anchors дадут candidate;
- полный parser не совпадёт;
- Rust вернёт `no_family` с parser error;
- простого наличия `%ASA-` и `106100` недостаточно для ложного успеха.

## 17. Где какие данные хранятся после всего цикла

| Данные | Место | Когда используются |
|---|---|---|
| Grok definitions | SQLite `grok_patterns` | Переиспользование и экспорт |
| Parser revisions | SQLite `parser_families` | Python dispatch, normalizer, export |
| Полный parser Response | SQLite `research_results` | Передача исходной семантики другим агентам |
| Normalization revision | SQLite `normalization_revisions` | Mapper и export |
| Mapping revision | SQLite `mapping_revisions` | Export |
| Taxonomy и embeddings | SQLite | Semantic mapping в Python |
| Bundle | JSON | Загрузка Rust |
| Скомпилированные regex | Память Rust | Parsing каждой строки |
| Aho–Corasick | Память Rust | Candidate dispatch каждой строки |
| Golden | JSON | Только parity tests |
| Итоговый event | stdout/JSONL | Потребитель pipeline |

Rust не обновляет SQLite, не сохраняет новые знания и не меняет Bundle. Обучение и исполнение разделены.

## 18. Проверка именно этого сквозного примера

Для текущего Bundle выполнено:

- 4/4 evidence прошли parser, normalization и MP SIEM validation;
- 4/4 Python golden results совпали с Rust по `ok`, `status`, captures, normalized и event;
- release CLI выбрал ровно одного candidate;
- release CLI вернул `status=mapped` для строки из этого отчёта;
- 10/10 Rust unit/integration tests прошли;
- `clippy -D warnings` и release build прошли.

Полная mapping-регрессия всех 1763 Cisco-строк не запускалась по отдельному решению пользователя. Отчёт доказывает сквозной путь на указанной строке и четырёх evidence, а не универсальную точность всех возможных Cisco-вариантов.

## 19. Артефакты

- `agent.py`, `prompts.py`, `tools.py` — parser control plane.
- `normalization_agent.py`, `normalization.py` — normalization control/execution.
- `mapping_agent.py`, `mapping.py`, `taxonomy.py` — mapping control/execution.
- `corpus.py` — исследовательская оркестрация трёх агентов.
- `bundle.py` — экспорт связанных revisions и dispatch metadata.
- `rust/log-data-plane/` — загрузка, dispatch и полный deterministic runtime.
- `research/r030-cisco-acl-verdict.bundle.json` — фактический Bundle `4/2/4`.
- `research/r030-cisco-acl-verdict.golden.json` — Python reference results.
- `research/030-rust-data-plane-review.md` — архитектура Rust и масштабирование dispatch.

[← R030](030-rust-data-plane-review.md) · [Все исследования](README.md)
