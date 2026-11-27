# R034 — сложный Symantec KV corpus через Rust: 997/997

> Цель: проверить полный pipeline на Symantec `grok → kv`, включая positional fields, quoting, пустые значения, IPv4/IPv6 и несколько payload-грамматик. Итог: все 997 KV-записей проходят Rust со `status=mapped`; полный Python ↔ Rust parity не имеет mismatches.

## 1. Итог

```text
KV RAW:            997
Mapped Rust:       997
Failed:              0
Golden evidence:   8/8
Parity mismatches:   0
```

| Family | Строк | Parser | Normalization | Mapping | Evidence |
|---|---:|---:|---:|---:|---:|
| `symantec_endpoint_traffic_log_symantecserver` | 943 | 2 | 3 | 3 | 6 |
| `symantec_endpoint_content_package_download_symantecserver` | 47 | 1 | 1 | 1 | 1 |
| `symantec_endpoint_system_event_symantecserver` | 7 | 1 | 1 | 1 | 1 |

Это первый полный end-to-end proof не только Grok, но и **KV pipeline в Rust**.

## 2. Область эксперимента

Исходный `artifact_edr_av.log` содержит 2129 строк смешанных форматов. Для задачи R034 выбраны все записи с `SymantecServer:` — ровно 997 KV-строк. CEF-часть исходного файла в этот эксперимент не входит.

Corpus сохранён без изменения порядка в:

```text
research/r034-symantec-kv.log
```

Payload distribution:

```text
21 колонка: 943 записи
7 колонок:   47 записей
6 колонок:    7 записей
```

Базовая Family из R026 была обучена только на одной 21-колоночной записи. Остальные форматы и значения были обнаружены полным corpus run.

## 3. Форматы и parsers

### 3.1. Traffic log

```text
RFC3164-like envelope
 → SymantecServer
 → 21 comma-delimited cells
 → labeled и positional values
```

Используется `grok → kv` с:

```json
{
  "pair_separator": ",",
  "key_value_separator": ":",
  "quotechar": "\"",
  "allow_positionals": true,
  "strip_values": true
}
```

Извлекаются 26 captures, включая envelope, body, labeled fields и positional `column_N`.

### 3.2. Content package download

Новая Family для 7-колоночного payload:

```text
symantec_endpoint_content_package_download_symantecserver@1
```

Она создаёт 12 captures и имеет собственные normalization/mapping.

### 3.3. System event

Новая Family для 6-колоночного payload с quoted `Event Description`:

```text
symantec_endpoint_system_event_symantecserver@1
```

Она создаёт 11 captures и корректно сохраняет запятую внутри quoted description.

## 4. Почему первого исследования R026 было недостаточно

Исходное evidence содержало только часть реальных вариантов. Полный corpus выявил:

```text
protocol: IP, UDP, ICMP
direction: Inbound, Outbound
action: Allowed, Blocked
IP: IPv4 и IPv6
required fields с пустыми значениями
```

Это не были ошибки KV tokenizer. Rust и Python извлекали поля, после чего строгие quality gates отклоняли неизвестные значения или несовместимый тип.

Примеры фактических failures:

```text
unmapped_source_value:column_9:UDP
unmapped_source_value:column_10:Outbound
unmapped_source_value:Action:Blocked
normalize_ip: expected ipv4, got ipv6
missing_required_field:Domain Name
```

Такое поведение предпочтительнее default-угадывания: неизвестные значения не скрывались.

## 5. Промежуточные результаты и repair

### 5.1. Первый прогон

Первый длительный runner был остановлен на новой грамматике около строки 923. Он создал traffic rev2 и content download Family, но не завершил corpus.

Добавлен `--start-line` в `corpus.py`, чтобы продолжать исходный диапазон без повторного запуска сотен завершённых строк. Progress теперь содержит `source_start_line` и `source_end_line`, а verify явно относится к выбранному диапазону.

### 5.2. Первый полный Rust

После завершения parser coverage и создания system event Family:

```text
804 mapped
169 mapping_failed
24 normalization_failed
```

Все 193 failures принадлежали traffic Family и сводились к трём классам:

- `Outbound`;
- `Blocked`;
- IPv6 normalization.

Content download 47/47 и system event 7/7 уже проходили Rust.

### 5.3. Evidence-driven rev3

В normalization/mapping agents добавлен `--evidence-file`. Дополнительные RAW:

- не заменяют прежние evidence;
- добавляются без дублей;
- участвуют в preview/evaluate;
- сохраняются в новой revision.

Repair evidence содержит шесть записей: исходные два и примеры всех фактических failure classes. Это не заменяет полный corpus: окончательная проверка выполнена на всех 997 строках.

Normalization rev3:

- IPv4/IPv6: `normalize_ip(version=any)`;
- сохраняет numeric/timestamp operations;
- проходит 6/6 evidence.

Mapping rev3:

- покрывает `Allowed` и `Blocked`;
- покрывает `Inbound` и `Outbound`;
- проходит 6/6 evidence;
- сохраняет строгое поведение для неизвестных значений.

После экспорта rev3 все ранние failure classes исчезли.

## 6. Финальный Rust прогон

Используются три Bundles:

```text
r034-symantec_endpoint_traffic_log_symantecserver.bundle.json
r034-symantec_endpoint_content_package_download_symantecserver.bundle.json
r034-symantec_endpoint_system_event_symantecserver.bundle.json
```

Итог:

```json
{
  "statuses": {
    "mapped": 997
  },
  "families": {
    "symantec_endpoint_traffic_log_symantecserver": 943,
    "symantec_endpoint_content_package_download_symantecserver": 47,
    "symantec_endpoint_system_event_symantecserver": 7
  }
}
```

Каждая запись прошла:

```text
Aho–Corasick dispatch
 → model Grok envelope
 → Rust KV codec
 → record schema validation
 → normalization
 → MP SIEM mapping
 → taxonomy validation
```

Во время этого прогона Python и LLM не вызывались.

## 7. Полный Python ↔ Rust parity

Для каждой из 997 строк сравнены:

```text
ok
status
captures
normalized
event
```

`research/r034-symantec-rust-full.summary.json`:

```json
{
  "ok": true,
  "total": 997,
  "mapped": 997,
  "failed": 0,
  "python_rust_parity": {
    "compared": 997,
    "mismatch_count": 0,
    "mismatches": []
  }
}
```

Это подтверждает идентичность всех промежуточных и финальных значений, а не только совпадение status.

## 8. Timestamp и конфигурация

BSD envelope timestamp не содержит год. Используется:

```text
source_default_year=2024
source_timezone=UTC
```

Год 2024 наблюдается в payload `Begin`/`Event time`; UTC — внешняя лабораторная настройка, а не вывод из RAW. Для production timezone должна приходить из ingestion metadata.

## 9. Изменённые компоненты

- `corpus.py` — `--start-line` и source-range resume.
- `normalization_agent.py` — `--evidence-file` и расширение evidence.
- `mapping_agent.py` — `--evidence-file` и вывод tool errors с `ok=false`.
- `prompts.py` — контракт дополнительных evidence без потери прежних.

Rust-код для достижения 997/997 не менялся: существующий Grok/KV runtime оказался достаточен после исправления knowledge revisions.

## 10. Вывод по оркестрации: staged learning

Одновременное обучение parser, normalization и mapping на первой строке оказалось неэффективной corpus-стратегией. Downstream-агенты видели слишком мало значений и создавали revisions, которые приходилось расширять после последующих строк.

Для следующих corpus рекомендуется:

```text
1. Весь corpus → только parser coverage
2. Family buckets и профили всех captures
3. Normalization с полным corpus validation
4. Mapping после стабилизации normalization
5. Полный regression и Bundle export
6. Rust и parity
```

Модели можно показывать профили и компактные variant examples, но quality gate обязан исполнять все записи Family. Это отдельный случай общей reconcile-архитектуры R035.

## 11. Совместный результат полных Rust regressions

```text
Cisco:    1763/1763
Juniper:    26/26
Symantec:  997/997
Итого:    2786/2786 mapped
Parity mismatches: 0
```

Используются семь Family Bundles. Подтверждены Grok и pipeline KV execution.

## 12. Артефакты

- `research/r034-symantec-kv.log` — 997 исходных KV-строк.
- `research/r034-symantec-mapping.db` — parser/normalization/mapping revisions.
- Три `.bundle.json` и три `.golden.json` для Symantec Families.
- `research/r034-symantec-traffic-repair-evidence.log` — шесть evidence rev3.
- `research/r034-symantec-rust-full.jsonl` — финальный результат каждой строки.
- `research/r034-symantec-rust-full.summary.json` — counts, revisions, hashes и parity.
- `research/r034-symantec-mapping.progress.jsonl` — история corpus run и resume.

## 13. Ограничения

- CEF-часть исходного файла в этот прогон не входила.
- Доказана parity с текущими Python revisions, а не независимая production-правильность всех semantic mappings.
- UTC остаётся лабораторной конфигурацией.
- Масштабный dispatch и ReDoS-hardening — отдельные задачи R030/R035.

[← R033](033-juniper-full-rust-review.md) · [Все исследования](README.md)
