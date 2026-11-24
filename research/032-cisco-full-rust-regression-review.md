# R032 — весь Cisco corpus через Rust: 1763/1763

> Цель: добиться полного прохождения `data/artifact_cisco_firewall.log` через native Rust pipeline. Итог: все 1763 строк имеют `status=mapped`, ошибок нет; Python и Rust совпали по `ok`, `status`, captures, normalized и event на каждой строке.

## 1. Итог

```text
Всего RAW:        1763
Mapped Rust:      1763
Failed:           0
Parity mismatches: 0
```

Распределение по Families:

| Family | Строк | Parser | Normalization | Mapping |
|---|---:|---:|---:|---:|
| `cisco_asa_acl_verdict` | 30 | 4 | 2 | 4 |
| `cisco_asa_acl_deny_log` | 1729 | 8 | 1 | 1 |
| `cisco_firepower_connection_event` | 4 | 1 | 2 | 1 |

Новые parser revisions не потребовались: исходная parser Registry уже покрывала все 1763 строк. Недоставало normalization и mapping для двух Families и внешнего года для Firepower timestamp.

## 2. Начальное состояние

Первый Rust прогон содержал только Bundle `cisco_asa_acl_verdict` из R030:

```text
mapped=30
no_family=1733
```

Python parser audit подтвердил:

- 30 строк принадлежат `cisco_asa_acl_verdict`;
- 1729 — `cisco_asa_acl_deny_log`;
- 4 — `cisco_firepower_connection_event`;
- неоднозначных и непокрытых parser-записей нет.

Следовательно, задача не требовала нового обучения парсера. Нужно было довести остальные Families до полного Bundle.

## 3. Создание недостающих revisions

### 3.1. `cisco_asa_acl_deny_log`

Parser revision 8 содержит шесть evidence с вариантами:

- ASA и PIX;
- IPv4 host, hostname и отсутствующий host;
- TCP с портами;
- ICMP с type/code;
- числовой `protocol 47`;
- необязательная пара hash-значений.

Normalizer создал revision 1:

- 19 NormalizationField;
- numeric PRI/severity/ports/ICMP type/code;
- IP normalization;
- timestamp с годом в naive ISO;
- 6/6 evidence прошли evaluation.

Mapper создал revision 1:

- 30 mappings;
- учтены conditional host и варианты протоколов;
- 6/6 evidence прошли MP SIEM schema validation.

### 3.2. `cisco_firepower_connection_event`

Parser revision 1 содержит 30 выходных полей и четыре совпадающие corpus-записи, хотя первоначальное evidence было одним.

Первая normalization revision 1 корректно отказалась выдумывать год для:

```text
Feb 23 12:47:35
```

В RAW отсутствуют и год, и timezone. Это сделало невозможным корректный MP SIEM `time` без внешней конфигурации.

Добавлена внешняя настройка:

```bash
--source-default-year 2023
```

Она передаётся в `NORMALIZATION_PROMPT` как `source_default_year` и используется только в `parse_timestamp.default_year` для формата без года. Год не выводится моделью из RAW и не подменяет timezone.

Для текущего corpus задано:

```text
source_default_year=2023  # по году соседних записей corpus
source_timezone=UTC       # внешняя лабораторная настройка
```

Normalizer создал revision 2:

- 30 NormalizationField;
- timestamp operations: `%b %e %H:%M:%S`, `%b %d %H:%M:%S`, `default_year=2023`;
- результат на evidence: `2023-02-23T12:47:35`.

Mapper создал revision 1:

- 39 mappings;
- 1/1 исходное evidence;
- MP SIEM `time=2023-02-23T12:47:35Z`.

Последующий полный прогон подтвердил все четыре Firepower-записи, а не только исходное evidence.

## 4. Найденный и исправленный Rust bug

Первый deny golden parity дал 5/6. На записи:

```text
Deny protocol 47 ...
```

Rust возвращал:

```text
missing_required_field:protocol
```

### Причина

Grok-конструкция содержит альтернативы:

```text
(?:protocol %{ASA_IP_PROTO_NUM:protocol}|%{ASA_DENY_PROTOCOL:protocol})
```

Кроме того, captures вложены в `body` и повторяются на втором parser layer.

Python `regex.groupdict()` возвращает последнюю участвовавшую группу с логическим именем. `fancy-regex` в такой вложенной конструкции терял повторное имя capture: простой `.name("protocol")` и последующий перебор `capture_names()` не давали значения для числовой ветви.

### Исправление

`compile_grok()` больше не использует логические имена непосредственно как Rust group names. Каждый `%{NAME:capture}` получает уникальное внутреннее имя:

```text
__grok_0
__grok_1
...
```

Компилятор сохраняет mapping:

```text
internal group name → логическое capture name
```

После совпадения `run_grok()` обходит группы в порядке regex и оставляет последнее участвовавшее значение для каждого логического имени. Это восстанавливает Python-семантику и работает для вложенных альтернатив и повторных слоёв.

Добавлены regressions:

- простой duplicate capture в альтернативе;
- фактическая deny-запись `protocol 47`.

После исправления:

```text
deny golden parity: 6/6
two Families Rust: 1759/1763 mapped
remaining: 4 Firepower без загруженного Bundle
```

## 5. Финальный Rust прогон

Команда:

```bash
./rust/log-data-plane/target/release/log-data-plane.exe run \
  --bundle research/r030-cisco-acl-verdict.bundle.json \
  --bundle research/r032-cisco-acl-deny-log.bundle.json \
  --bundle research/r032-cisco-firepower.bundle.json \
  --input data/artifact_cisco_firewall.log \
  --output research/r032-cisco-rust-full.jsonl
```

Результат:

```json
{
  "results": 1763,
  "statuses": {
    "mapped": 1763
  },
  "families": {
    "cisco_asa_acl_verdict": 30,
    "cisco_asa_acl_deny_log": 1729,
    "cisco_firepower_connection_event": 4
  },
  "failure_count": 0
}
```

Каждая строка прошла:

```text
Aho–Corasick dispatch
 → полный parser и schema validation
 → normalization
 → MP SIEM mapping
 → taxonomy validation
```

## 6. Полный Python ↔ Rust parity

Дополнительно к успешным статусам выполнено независимое сравнение на всех 1763 строках.

Для каждой RAW использованы одинаковые Family Bundles и сравнены:

```text
ok
status
captures
normalized
event
```

Итог из `research/r032-cisco-rust-full.summary.json`:

```json
{
  "ok": true,
  "total": 1763,
  "mapped": 1763,
  "failed": 0,
  "python_rust_parity": {
    "compared": 1763,
    "mismatch_count": 0,
    "mismatches": []
  }
}
```

Это сильнее предыдущего результата R030, где проверялись только четыре evidence одной Family. Полная mapping-регрессия теперь выполнена по прямому заданию пользователя.

## 7. Дополнительные проверки

- Rust unit/integration tests: **12/12**;
- `cargo fmt --check`: успех;
- `cargo clippy --all-targets -- -D warnings`: успех;
- release build: успех;
- deny golden: **6/6**;
- Firepower golden: **1/1**;
- исходный ACL verdict golden: **4/4**;
- полный Rust corpus: **1763/1763**;
- полный Python/Rust parity: **1763/1763**, mismatches=0;
- `py_compile` для изменённых Python-файлов: успех.

## 8. Изменённые компоненты

- `normalization_agent.py` — `--source-default-year` и передача настройки в prompt.
- `corpus.py` — сквозная передача `source_default_year` в normalizer.
- `prompts.py` — явный контракт внешнего года: не выводить из RAW, применять только к timestamp без года.
- `rust/log-data-plane/src/parser.rs` — уникальные internal capture names и Python-совместимое восстановление логических captures.
- `research/r028-cisco-mapping.db` — новые normalization/mapping revisions и research results.

## 9. Артефакты

- `research/r032-cisco-acl-deny-log.bundle.json`;
- `research/r032-cisco-acl-deny-log.golden.json`;
- `research/r032-cisco-firepower.bundle.json`;
- `research/r032-cisco-firepower.golden.json`;
- `research/r032-cisco-rust-full.jsonl` — финальный результат каждой строки;
- `research/r032-cisco-rust-full.summary.json` — revisions, counts, source configuration и parity summary.

Промежуточные JSONL начального и двухсемейного прогонов удалены после фиксации результатов; финальный полный результат сохранён.

## 10. Ограничения

- `source_default_year=2023` и `source_timezone=UTC` — внешние лабораторные настройки. Для production их нужно получать из конфигурации источника или ingestion metadata.
- Доказана эквивалентность текущим Python revisions, а не независимая семантическая правильность каждого mapping относительно golden production MP SIEM.
- Масштабный dispatch на 10 000 Families и ReDoS-hardening остаются отдельными задачами R030.

[← R031](031-cisco-end-to-end-pipeline-review.md) · [Все исследования](README.md)
