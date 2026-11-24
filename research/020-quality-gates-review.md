# R020 — детерминированные quality gates

> Цикл улучшений R018 по аудиту R019. Добавлены межполевые constraints, source-path validation, исполняемые Grok examples и latest-first dispatch. Полный corpus снова обработан с пустого Registry: 1763/1763 покрыто, 10 ReAct-исследований, `failed=0`.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R020` |
| Дата | 23 ноября 2026 г. |
| Baseline для сравнения | `R018`, аудит `R019` |
| Начальное состояние | Новая пустая SQLite БД, 0 встроенных Grok |
| Registry | `research/r020-corpus.db` |
| Corpus | 1763 строки |
| ReAct-исследования | 10 |
| Итоговые Family | 3 |
| Parser revisions | 12 после удаления одного invalid candidate |
| Grok | 49, все с исполняемыми examples |
| Semantic embeddings | 73 |
| Итог | Все quality gates и полный corpus проходят |

## Гипотезы

| ID | Гипотеза | Результат |
|---|---|---|
| H1 | Межполевые constraints устранят ложные комбинации без потери покрытия | Подтверждена |
| H2 | Source paths можно проверять детерминированно | Подтверждена: старые 66 ошибок ловятся, новые responses — 0 |
| H3 | Примеры и зависимости Grok можно проверять исполнением | Подтверждена: 49/49 конструкций проходят |
| H4 | Latest-first уменьшит шум revisions без потери покрытия | Подтверждена: ровно один match на каждую строку |

## H1 — constraints и диапазоны

В `ParserDefinition` добавлены `FieldConstraint`:

```text
allowed_when  — где вообще может присутствовать capture
required_when — при каких значениях других captures оно обязательно
```

Они проверяются одинаково в ReAct, `evaluate_candidate`, финальной проверке и corpus dispatch.

Примеры фактических правил R020:

- `src_port` и `dst_port` допустимы и обязательны только для портовых протоколов;
- `icmp_type` и `icmp_code` допустимы только для ICMP;
- `tcp_flags` допустим только для TCP;
- завершающая hex-пара обязательна для ASA-варианта, но не для PIX без хвоста.

Диапазоны проверяются отдельно:

- syslog PRI: 0–191;
- порты: 0–65535.

### Negative verification

| Negative case | Результат |
|---|---|
| TCP с `(type 3, code 3)` | `forbidden_field:icmp_type/icmp_code` |
| TCP без src/dst портов | `missing_conditional_field` |
| ICMP без type/code | `missing_conditional_field` |
| UDP с `tcp_flags` | `forbidden_field:tcp_flags` |
| PRI 192 | `invalid_pri_range` |
| PRI 255 | `invalid_pri_range` |

При этом все 1763 положительных строк остаются покрытыми.

## H2 — contract source validation

`validation.check_response` теперь требует:

```text
record_schema.source ∈ structure.path
```

Для raw используется канонический путь `$`.

Проверка показала:

- все 66 несовпадений R019 детектируются автоматически;
- во всех 10 новых research responses несовпадений нет;
- исправленный source и сохранённая схема принимаются.

Также исправлен lifecycle SQLite-соединений: теперь успешная транзакция коммитится, соединение закрывается и на Windows БД не остаётся заблокированной.

## H3 — исполняемые Grok examples

`GrokPattern` дополнен полями:

```text
positive_examples[]
negative_examples[]
```

Для каждой новой конструкции требуется минимум один пример каждого типа. `validate_patterns`:

1. компилирует все зависимости `%{NAME}`;
2. требует наличие examples;
3. исполняет положительные примеры и ждёт совпадения;
4. исполняет отрицательные примеры и ждёт отказа.

`get_grok_pattern` и semantic search возвращают вычисленные dependencies, поэтому их не нужно поддерживать вручную в описании.

Проверено:

- правильный positive/negative набор принимается;
- negative case, совпадающий с regex, отклоняется;
- отсутствующая зависимость возвращает `unknown_grok_pattern`;
- examples сохраняются и загружаются из SQLite;
- все 49 итоговых Grok проходят независимую проверку.

### Gate при сохранении Family

После первого corpus run независимая проверка обнаружила ошибки в двух промежуточных вариантах:

- `ASA_ACL_NAME` содержал examples с кавычками, несовместимыми с определением `[^\"]+`;
- промежуточный `cisco_asa_acl_deny_log@6` принимал negative example с `106100`.

Поэтому `save_parser_family` теперь вызывает `validate_patterns` **до записи**. Невалидная Family возвращает `grok_pattern_validation_failed` и не попадает в Registry. Отдельный тест подтвердил блокировку такого сохранения.

Invalid revision 6 удалён из итогового Registry, examples `ASA_ACL_NAME` исправлены. Рабочие latest revisions не изменялись.

## H4 — latest-first dispatch

Новый порядок:

```text
RAW
→ исполнить latest revision каждой Family
→ если совпадение есть: принять
→ иначе исполнить исторические revisions как fallback
→ иначе ReAct
```

Semantic search parser revisions тоже работает только с latest. История доступна через `get_parser_family(family_id, revision)`, но не шумит обычный поиск.

Результат на R018:

```text
{1 match: 1763 строки}
```

Тот же результат после R020 и quality repair. Разные Family не дают неоднозначного совпадения; такой случай теперь завершается `ambiguous_family_match`, а не выбором первой.

## Полный R020 corpus

| Показатель | R018 | R020 |
|---|---:|---:|
| Всего строк | 1763 | 1763 |
| ReAct-исследования | 9 | 10 |
| Покрыто без модели | 1754 | 1753 |
| Family | 3 | 3 |
| Parser revisions | 9 | 12 |
| Grok | 48 | 49 |
| Source mismatches | 66 | 0 |
| Grok с проверенными examples | 0 | 49 |
| Matches latest на строку | Множество | 1 |
| Непокрыто | 0 | 0 |
| Ошибки `failed` | 0 | 0 |

Дополнительная исследованная строка по сравнению с R018 — 1748: ICMP `type 11, code 0` с hostname.

### Исследованные наблюдения

| Строка | Принятые revisions |
|---:|---|
| 1 | `cisco_asa_acl_verdict@1`, `@2` после исправлений |
| 21 | `cisco_asa_acl_deny_log@1`, `@2` после исправлений |
| 25 | `cisco_asa_acl_deny_log@3` |
| 31 | `cisco_asa_acl_verdict@3` |
| 104 | `cisco_asa_acl_deny_log@4` |
| 201 | `cisco_asa_acl_deny_log@5` |
| 853 | `@7`; промежуточный `@6` отклонён аудитом |
| 1627 | `cisco_asa_acl_verdict@4` |
| 1748 | `cisco_asa_acl_deny_log@8` |
| 1760 | `cisco_firepower_connection_event@1` |

## Независимая финальная проверка

После corpus и quality repair подтверждено:

- latest responses трёх Family проходят `check_response`;
- все source paths остальных responses также корректны;
- 49/49 Grok проходят examples и dependencies;
- все negative cases отклоняются;
- latest-first покрывает все 1763 строки ровно одним match;
- invalid Family блокируется при сохранении;
- итоговая машинная сводка соответствует фактическим счётчикам БД.

`r020-corpus.progress.jsonl` содержит отдельное событие `quality_repair`, поэтому постобработка не скрыта в исходном `COMPLETE`.

## Оставшиеся ограничения

- Нет полноценного lifecycle `candidate → validated → published → deprecated`; latest — технический порядок revision, а не публикационный статус.
- Старые revisions остаются fallback и несут исторические слабые описания, поэтому production migration policy ещё нужен.
- Проверка examples доказывает их совместимость с regex, но не истинность свободного описания.
- Канонической нормализации protocol, interval и timestamp по-прежнему нет: это лексический parser.
- Corpus содержит только три Family; качество для неизвестных источников не доказано.
- Rerun после добавления save gate не повторялся с нуля; gate проверен отдельно, а итоговый Registry прошёл независимый аудит.

## Вывод

**R020 — новый исследовательский baseline.** Главный результат не повторные 1763/1763, а переход от «модель сказала, что parser работает» к обязательным машинным проверкам:

- грамматические границы;
- source-path contract;
- Grok dependencies и examples;
- сохранение Family;
- актуальный dispatch;
- независимый post-run аудит.

## Артефакты

- `research/r020-corpus.db` — итоговый Registry.
- `research/r020-corpus.summary.json` — обновлённая машинная сводка.
- `research/r020-corpus.progress.jsonl` — corpus и `quality_repair`.
- `research/020-quality-gates-review.md` — настоящий отчёт.
- `schemas.py`, `grok.py`, `validation.py`, `corpus.py`, `storage.py`, `semantic.py` — реализация quality gates.

[← R019](019-model-quality-audit.md) · [Все исследования](README.md)
