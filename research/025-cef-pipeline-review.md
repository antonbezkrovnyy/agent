# R025 — модельный Grok envelope и CEF pipeline

> Эксперимент остановлен пользователем после подтверждения основной гипотезы. Завершены 66 из 1132 CEF-строк: 6 ReAct-исследований и 60 повторных покрытий, `failed=0`. Седьмая Family сохранена на прерванной строке 67 и отдельно проверена.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R025` |
| Дата | 24 ноября 2026 г. |
| Baseline | Копия `research/r023-solaris.db` |
| Вход | Строки 1–1132 `artifact_edr_av.log` |
| Формат | BSD-syslog envelope + Trend Micro CEF |
| Обработано | 66 из 1132 |
| Итог | Остановлено пользователем; целевой pipeline подтверждён |

## Целевая архитектура

Модель сама исследует конверт и пишет Grok. Capture с payload передаётся в `probe_format`, после чего детерминированный CEF-кодек разбирает заголовок и extension:

```text
RAW → model Grok envelope → probe(payload) → CEF codec → validation → Family
```

Готовый syslog-codec из pipeline исключён. Pipeline хранится в той же `parser_families`: у Grok-revisions заполнены `patterns/layers`, у pipeline-revisions — `stages_json`. Старые responses и 67 Solaris-строк прошли compatibility check.

CEF-кодек поддерживает семь полей заголовка, пробелы в незакавыченных значениях extension, quoted values и escape-последовательности. До ReAct-прогона независимый preflight с модельным Grok-конвертом прошёл все 1132 строки без ошибок. Это проверка исполнителя, а не заявление о полном модельном покрытии.

## Что создала модель

Созданы семь pipeline-Families:

- AWS Account Synchronization Requested (`1908`);
- AWS Account Synchronization Finished (`1909`);
- Relay Group Update Requested (`1600`);
- Scheduled Task Started (`564`);
- DPI Command Injection (`1005934`);
- IIS Directory Traversal (`1006434`);
- XSS Prevention (`1000552`, прерванное исследование строки 67).

У всех сохранены две стадии: `grok → cef`. Модель самостоятельно использовала стабильные CEF-заголовки как dispatch: vendor/product/signature фиксируются в Grok, а `constraints.equals` дополнительно проверяет ключ после разбора. Это предотвращает поглощение всех CEF-событий одной универсальной Family.

## Reuse и полнота разбора

- 60 строк покрыты сохранёнными Families без обращения к исследованию.
- Manager Families извлекают по 17 captures.
- Agent Families извлекают по 39 captures.
- У всех семь Families evidence исполняется с пустым residual и без validation issues.
- Все 66 завершённых строк имеют ровно одно совпадение parser.

Модель включает CEF-поля в `record_schema`. Семантические псевдонимы вроде `src → src_ip` и `act → action` допустимы: исходные captures сохраняются, а последующий маппинг будет использовать описания и `source`.

## Независимая проверка

- Все 6 завершённых research responses проходят `check_response`.
- Все 7 новых pipeline-Families проходят исполнение и validation на своих evidence.
- Все 295 глобальных Grok проходят dependencies и positive/negative examples.
- `failed=0`; обработанные строки имеют один parser match.

### Ограничение Registry

Из 21 уникального Grok-определения, встроенного в envelope-стадии, 13 отсутствуют в глобальном `grok_patterns`. Они исполняются внутри pipeline, но недоступны глобальному semantic search и не имеют самостоятельных examples. Это не мешает текущему разбору, но требует отдельного улучшения save gate в следующих итерациях.

## Стоимость исследования

Новое ReAct-исследование занимало примерно 108–222 секунды, медиана около 2,5 минут. Повторное покрытие — около 0,39 секунды на строку. Основное время уходит на последовательные обращения к LLM и подробные описания, а не на CEF-кодек. По этой причине прогон остановлен после доказательства архитектуры, без ожидания всех вариантов `signature_id`.

## Итоговый Registry

| Объект | Количество |
|---|---:|
| Family | 67 |
| Parser revisions | 93 |
| Новые pipeline-Families | 7 |
| Grok | 295 |
| Research results | 83 |
| Semantic embeddings | 481 |

## Ограничения

- Строки 67–1132 не считаются обработанными, хотя Family для строки 67 сохранена и валидна.
- Symantec-строки 1133–2129 в эксперимент не входили.
- Нет независимого golden-эталона смысла всех extension-полей.
- Проверяется парсинг, а не нормализация или маппинг в таксономии.
- R023 остаётся последним полным текстовым baseline; R025 — частичное подтверждение structured pipeline.

## Артефакты

- `pipeline.py` — model-Grok stage, CEF codec, probe, executor и evaluation.
- `research/r025-cef.db` — частичный Registry.
- `research/r025-cef.summary.json` — машинная сводка.
- `research/r025-cef.progress.jsonl` — progress, остановка и quality audit.

[← R024](024-communigate-review.md) · [Все исследования](README.md)
