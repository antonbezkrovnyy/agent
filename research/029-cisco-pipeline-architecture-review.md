# R029 — итоговая архитектура: RAW → MP SIEM

> Исследовательская цепочка из трёх независимых агентов собрана и проверена на Cisco. Parser и normalization уже имеют полную регрессию 1763 строк. MP SIEM mapping проверен на четырёх evidence и одном end-to-end runner; полная mapping-регрессия по решению пользователя не выполнялась.

## 1. Итоговый результат

Для Cisco `cisco_asa_acl_verdict@4` реализованы:

- parser revision 4;
- normalization revision 2;
- MP SIEM mapping revision 4;
- 18 подробных NormalizationField descriptions;
- 29 mappings в `mp_siem_8.0`.

Итоговый event содержит обязательный каркас MP SIEM, категории, субъект, src/dst, protocol, msgid, счётчики и дополнительные datafield/numfield. Исходные RAW и captures не перезаписываются.

## 2. Три агента

| Агент | Вход | Результат | Основные инструменты |
|---|---|---|---|
| Parser | RAW и Registry | captures, parser revision | Grok/CEF/CSV/KV execution, candidate evaluation, Family storage |
| Normalizer | Family, parser research, evidence | normalized values, подробные описания полей | preview/evaluate/save normalization, get_parser_research |
| Mapper | Normalized values и их описания | Плоское событие MP SIEM | Semantic taxonomy search, guidelines, preview/evaluate/save mapping |

Нормализатор получает исходный Response парсинг-агента через `get_parser_research`, а не восстанавливает смысл заново. Mapper получает исходный Response и normalization descriptions.

## 3. Taxonomy Registry

Справочник `reference/mpsiem` преобразован в SQLite:

- 212 полей;
- 232 Enum values;
- 222 категории;
- 666 taxonomy embeddings, 739 embeddings всего в R028 Registry.

Mapper ищет кандидатов по текстовым описаниям через `semantic_search_taxonomy`, затем подтверждает их точными определениями. Отдельный `get_mapping_guidelines` передаёт полный порядок маппинга из MP SIEM reference.

## 4. Единый runner

Общий runner уже поддерживает:

```text
corpus.py --map-taxonomy mp_siem_8.0 --source-timezone UTC
```

Последовательность:

```text
RAW
 → latest-first parser dispatch
 → normalization revision для parser revision
 → mapping revision для normalization revision
 → MP SIEM event
```

Если revision отсутствует, вызывается соответствующий агент. На повторных событиях используются только сохранённые правила.

Проверка runner на одной записи:

```text
COVERED cisco_asa_acl_verdict@4
NORMALIZATION normalized
MAPPING mapped
```

Для пользовательского API остаётся добавить тонкий вариант `одна строка → final event`, но исследовательская оркестрация уже работает.

## 5. Проверки

### Parser и normalization

- 1763/1763 Cisco-строк имеют один parser match.
- 30/30 записей соответствующей Family нормализованы.
- 18 полей имеют развёрнутые описания.
- Исходные captures не изменяются.
- Timestamp без timezone превращается в naive ISO без выдуманного `Z`.

### Mapping

- 4/4 evidence проходят parser, normalization и MP SIEM schema.
- Все девять обязательных полей присутствуют.
- Порядок mappings соответствует `mp_siem_8_mapping_sequence`.
- Enum values и category hierarchy валидны.
- `event_src.ip` и `event_src.hostname` выбираются условно по представлению host.
- `datafield1..10` и `numfield1..5` присутствуют и исполняются.
- Полная mapping-регрессия 1763 строк по решению пользователя не запускалась.

## 6. Сравнение с production-событием

Совпали:

- `action=deny`;
- `object=connection`;
- `status=success`;
- `subject=rule`;
- имя ACL;
- vendor/title/category источника;
- src/dst адреса и порты;
- `msgid=106100`.

Различаются:

- ветвь категории события;
- `importance`;
- `id`;
- регистр protocol и типы портов;
- export-имена полей и автоматические/технические поля.

Ветвь `Connection / Network Interaction Management / Control` из production-примера отсутствует в переданном справочнике 8.0, поэтому напрямую сравнивать их как одинаковые версии таксономии нельзя. Основной semantic mapping подтверждён, точная production-совместимость требует калибровки по целевой версии и правилам заказчика.

## 7. История mapping revisions

- Rev1 выявила отсутствие полного guideline, порядка, namespace и условного host.
- Rev2 исправила порядок и `object=connection`, но обнаружила bug условного IP.
- Rev3 исправила conditional host и выявила отсутствие datafields.
- Rev4 добавила все datafields, исправила conditional `event_src.ip` и прошла проверки.

Дополнительно исправлен parser Markdown-диапазонов: `datafield1 … datafield10` теперь превращается в десять полей, `numfield1 … numfield5` — в пять.

## 8. Rust + VRL

Рекомендуемое разделение:

```text
Python control plane                  Rust data plane
agents создают revisions     →       Rust + VRL исполняют approved bundles
SQLite/semantic Registry     →       versioned Family Bundles
candidate/baseline checks    →       latest-first dispatch и validation
```

Компилировать нужно не агентов, а версионированный `Family Bundle`:

- parser definition;
- normalization rules;
- mapping rules;
- taxonomy version;
- descriptions;
- evidence и compatibility hash.

VRL подходит для преобразований и assignments. Rust нужен для нестандартных codecs, taxonomy validation, dispatch и атомарного обновления bundles. Обязательное условие переноса — golden parity `Python result == Rust result` на одних и тех же RAW.

## 9. Ограничения

- `source_timezone=UTC` — внешняя лабораторная настройка, а не свойство Cisco RAW.
- Полная mapping-регрессия не запускалась.
- Глубина свободных описаний не имеет полного golden-эталона.
- Составное `устройство/интерфейс` не записывается в неподходящее MP SIEM field.
- Production-версия категорий и важности может отличаться от исследовательской mapping revision.

## 10. Артефакты

- `normalization.py`, `normalization_agent.py` — normalization layer.
- `taxonomy.py` — Taxonomy Registry, semantic search и schema validation.
- `mapping.py`, `mapping_agent.py` — MP SIEM mapping layer.
- `runtime.py` — общий parser executor.
- `research/r027-cisco-normalization.db` — normalization baseline.
- `research/r028-cisco-mapping.db` — mapping revisions и Taxonomy Registry.
- `research/027-cisco-normalization-review.md` — детали normalization.
- `research/028-cisco-mp-siem-mapping-review.md` — детали mapping.

[← R028](028-cisco-mp-siem-mapping-review.md) · [Все исследования](README.md)
