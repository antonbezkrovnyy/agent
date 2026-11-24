# R026 — Symantec positional KV после CEF

> Одна заданная Symantec-строка полностью исследована за 192 секунды. Модель выбрала `grok → kv`, создала Family `symantec_endpoint_traffic_log_symantecserver@1` и извлекла 26 captures. Независимая проверка прошла.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R026` |
| Дата | 24 ноября 2026 г. |
| Baseline | Копия `research/r025-cef.db` |
| Вход | SymantecServer network traffic, 21 позиция payload |
| Research result | `84` |
| Итог | 1/1 обработано, `failed=0` |

## Выбор формата моделью

Вход имеет два уровня:

```text
RFC3164-like syslog → SymantecServer: <comma-delimited payload>
```

Payload содержит подписанные значения `Local Host IP: ...` и позиционные `IP,Inbound`. Probe предложил два кандидата — CSV и KV. Модель выбрала KV со следующей конфигурацией:

```json
{
  "pair_separator": ",",
  "key_value_separator": ":",
  "quotechar": "\"",
  "allow_positionals": true,
  "strip_values": true
}
```

Итоговый pipeline:

```text
model Grok envelope → KV payload
```

Grok снимает syslog и фиксирует `SymantecServer` как `msg_id` для dispatch. KV разбирает все 21 позиции, сохраняет пустые значения и даёт позиционным ячейкам имена `column_N`. Family не использует гигантскую регулярку на каждое поле.

## Зачем первый запуск был остановлен

До добавления CSV/KV модель начала строить 21-поле Grok и исправлять границы пустых значений вручную. Это был неправильный путь для структурного payload, поэтому запуск остановлен до сохранения research result. В отчёт включается только повторный завершённый эксперимент с KV.

## Расширенная конфигурация CSV/KV

После первого успешного исследования конфигурация кодеков расширена без изменения старых defaults.

CSV поддерживает:

- `delimiter`, `quotechar`, `escapechar`, `doublequote`, `skipinitialspace`, `strict`;
- `label_separator`, `positional_prefix`, `positional_names`, `field_names`;
- `strip_keys`, `strip_values`, `allow_positionals`;
- `allow_empty_keys`, `allow_empty_fields`;
- `expected_columns`, `duplicate_policy=error|first|last`.

KV поддерживает:

- `pair_separator`, `key_value_separator` и те же quoting-настройки;
- `positional_prefix`, `positional_names`;
- `allow_positionals`, `allow_empty_keys`, `allow_empty_values`;
- `expected_pairs`, `duplicate_policy=error|first|last`.

Базовый tokenizer — стандартный `csv.reader`. Дополнительный код проверяет параметры и преобразует ячейки в поля pipeline.

## Проверки

- Финальный Response `84` проходит `check_response`.
- RAW имеет ровно одно совпадение — `symantec_endpoint_traffic_log_symantecserver@1`.
- Все 8 pipeline-Families из R025/R026 проходят evidence после расширения конфигурации.
- Все 297 глобальных Grok проходят dependencies и examples.
- Расширенные параметры проверены на quoting, escaping, multi-character separators, duplicates, empty values, field naming и ошибках параметров.
- CSV/KV preflight прошёл все 997 Symantec-строк внешнего corpus: 943 с 21 колонкой, 47 с 7 и 7 с 6. Это проверка кодека, а не заявление о модельном покрытии всего corpus.

## Итоговый Registry

| Объект | Количество |
|---|---:|
| Family | 68 |
| Parser revisions | 94 |
| Grok | 297 |
| Research results | 84 |
| Semantic embeddings | 485 |

## Ограничения

- Моделью обработана только одна заданная строка, а не все 997 Symantec-записей.
- Две другие Symantec-грамматики показаны codec preflight, но не исследованы моделью.
- Семантическая правильность каждого поля не подтверждена независимым golden-эталоном.
- Нормализация и маппинг не выполняются.

## Артефакты

- `research/r026-symantec.db` — Registry с новой KV-Family.
- `research/r026-symantec.summary.json` — машинная сводка.
- `research/r026-symantec.progress.jsonl` — research и quality audit.
- `pipeline.py` — расширенные CEF/CSV/KV codecs.

[← R025](025-cef-pipeline-review.md) · [Все исследования](README.md)
