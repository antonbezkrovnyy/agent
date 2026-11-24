# R028 — Cisco в MP SIEM: третий агент и семантическая таксономия

> Полный pipeline `parser → normalization → MP SIEM mapping` проверен на четырёх evidence. Latest mapping revision 4 содержит 29 соответствий. Общий runner на одной записи прошёл `COVERED → NORMALIZATION → MAPPING`. Полная регрессия 1763 строк по решению пользователя не выполнялась.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R028` |
| Дата | 24 ноября 2026 г. |
| Baseline | Копия `research/r027-cisco-normalization.db` |
| Family | `cisco_asa_acl_verdict@4` |
| Normalization | `2` |
| Mapping | `4` |
| Таксономия | `mp_siem_8.0` |
| Mapping research | `15` |
| Проверка | 4/4 evidence, 29 mappings, 1/1 runner |

## Три отдельных агента

1. **Parser** строит Grok/structured pipeline и извлекает captures.
2. **Normalizer** описывает поля и детерминированно преобразует значения.
3. **Mapper** по этим описаниям проектирует соответствие MP SIEM.

Mapper получает `get_parser_family`, `get_parser_research` и `get_normalization`, поэтому использует исходную essence/structure/pitfalls и развёрнутые NormalizationField descriptions, а не только имена captures.

## Taxonomy Registry и semantic search

Справочник `reference/mpsiem` загружен в SQLite:

- 212 полей с типами, обязательностью и automatic flag;
- 232 Enum values;
- 222 категории;
- 666 taxonomy embeddings, 739 embeddings всего в Registry.

Инструменты mapper:

- `get_mapping_guidelines` — полный порядок из `mp_siem_8_mapping_sequence.md`;
- `semantic_search_taxonomy` — кандидаты среди полей, Enum values и категорий по описаниям;
- `get_taxonomy_field`, `get_taxonomy_values`, `get_taxonomy_categories`;
- `preview_mapping`, `evaluate_mapping`;
- `save_mapping`, `get_mapping`.

Semantic search подтвердил релевантность: `src.port` и `msgid` получили первый rank, `Firewall Rules Usage` — первая категория, `deny` — среди первых action-кандидатов.

## Что создала модель в rev4

Обязательный каркас:

- `time` из normalized timestamp с внешней лабораторной настройкой `source_timezone=UTC`;
- `id=mp_cisco_asa_syslog_acl_verdict` с отдельным namespace;
- `event_src.vendor=cisco`, `event_src.title=asa`, `event_src.category=Firewall`;
- `denied → deny`, `permitted → allow`;
- `object=connection`;
- `status=success` для обоих вердиктов: действие межсетевого экрана выполнено;
- `importance` по ASA severity 0..7.

Категория события:

```text
Accounting → Network Accounting → Firewall Rules Usage
```

Субъект — `rule`, `subject.name` — имя ACL. Network src/dst перенесены в `src.ip/src.port` и `dst.ip/dst.port`. `protocol`, `msgid`, `hit_cnt → count.packets` и source interface также смаплены.

`host` обрабатывается условно:

- IP-форма → `event_src.ip`;
- hostname-форма → `event_src.hostname`;
- составное `устройство/интерфейс` не записывается в неподходящее поле.

Дополнительные данные:

- `hit_interval → datafield1`;
- `packet_hash → datafield2`;
- `hit_hash → datafield3`;
- `dst_iface → datafield4`;
- `pri → numfield1`.

Opaque-хэши сохранены без интерпретации, `dst_iface` не склеен с source interface.

## История revisions

- **Rev1:** нет полного guideline в инструментах, порядок нарушен, `id` без namespace, host ошибочно всегда в hostname, `object=packet` требовал пересмотра.
- **Rev2:** порядок и `object=connection` исправлены; обнаружен bug условного IP и проблема datafield Registry.
- **Rev3:** conditional `event_src.ip` исправлен; выявлен parser-дефект диапазона `datafield1 … datafield10`.
- **Rev4:** все 15 datafield/numfield загружены, условный host и дополнительные поля проходят.

Первые revisions оставлены как диагностическая история, latest — rev4.

## Проверки

- Все 4 evidence проходят parser, normalization и mapping.
- Все девять обязательных полей присутствуют.
- Порядок mappings соответствует `mp_siem_8_mapping_sequence`.
- Enum values и category hierarchy валидны.
- IP/hostname condition работает без падения на составном значении.
- `datafield1..10` и `numfield1..5` подтверждены в SQLite и в исполнении.
- Общий runner с `--map-taxonomy mp_siem_8.0` на одной строке выполнил parser, normalization и mapping без обращения к LLM.
- Полная регрессия corpus не запускалась по явному решению пользователя.

## Ограничения

- Проверена одна Cisco Family и её четыре evidence, а не все варианты corpus.
- `source_timezone=UTC` — внешняя настройка лабораторного эксперимента, не вывод из Cisco RAW.
- Составное `устройство/интерфейс` остаётся в normalized representation, но не записывается в неподходящее MP SIEM field.
- Семантические решения модели проверены по справочнику и evidence, но не независимым golden-набором MP SIEM.

## Артефакты

- `taxonomy.py` — Taxonomy Registry, semantic search и schema validation.
- `mapping.py` — generic mapping executor.
- `mapping_agent.py` — отдельный mapper ReAct.
- `research/r028-cisco-mapping.db` — mapping revisions и Taxonomy Registry.
- `research/r028-cisco-mapping.summary.json` — машинная сводка.
- `research/r028-cisco-mapping.progress.jsonl` — runner и quality audit.

[← R027](027-cisco-normalization-review.md) · [Все исследования](README.md)
