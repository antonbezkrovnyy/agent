# R027 — минимальная нормализация Cisco после парсинга

> Первая работающая normalization pipeline для `cisco_asa_acl_verdict@4`. Все 18 полей получили развёрнутые описания, operations проверены на 4 evidence, полный Cisco corpus сохранил 1763/1763 parser matches. Latest normalization revision 2 нормализует 30 соответствующих записей без ошибок.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R027` |
| Дата | 24 ноября 2026 г. |
| Baseline | Копия `research/r020-corpus.db` |
| Family | `cisco_asa_acl_verdict@4` |
| Исходный parser research | `8` |
| Normalization research | `12` |
| Latest normalization | `2` |
| Итог | 18 описаний, 4/4 evidence, 30/30 нормализаций, parser 1763/1763 |

## Итоговый pipeline

С флагом `--normalize` обработка идёт так:

```text
RAW
 → latest-first parser dispatch
 → исходные captures
 → normalization revision для точной parser revision
 → normalized values
```

Если parser отсутствует, сначала работает parser ReAct и сохраняет Family. Если normalization отсутствует, вызывается normalization agent. При повторной записи обе revisions переиспользуются без LLM. Без `--normalize` сохраняется прежний parse-only режим.

Возвращаются независимые `parse_ok` и `normalization_ok`; ошибка нормализации не отменяет успешный разбор. Исходные RAW и captures не изменяются.

## Передача исходной семантики

Rev1 использовала только `get_parser_family`: record_schema, evidence, constraints и Family/parser descriptions. Это было недостаточно для полной передачи трактовки.

В rev2 добавлен `get_parser_research`: он находит исходный Response по точному совпадению parser definition. Нормализатор получил research `8` с `essence`, 20 structure layers, 8 strategy decisions, 12 format features и 8 pitfalls. Он не восстанавливал смысл заново.

## Поля и описания

Normalization revision содержит 18 `NormalizationField`:

- `source` — исходное имя capture;
- `description` — смысл, участники, направление, единицы, допустимые значения, null/zero и неопределённости;
- `operations` — упорядоченные преобразования;
- `required` — обязательность после преобразования.

`target` отсутствует: имя результата совпадает с исходным. Поля без операций также получили подробные описания.

Примеры:

- `hit_cnt` описан как агрегированный ASA-счётчик, а не число RAW-строк;
- `hit_interval` сохраняет `first hit` и `N-second interval` без допарсинга;
- `packet_hash` и `hit_hash` остаются непрозрачными строками с неподтверждённым смыслом;
- `host` является необязательным отправителем syslog, а не источником трафика; при отсутствии сегмента capture отсутствует;
- timestamp содержит год, но не timezone, поэтому превращается в naive ISO без выдуманного `Z`.

Rev1 содержала неточность `host=null`; metadata исправлена на отсутствующий capture. Rev2 изначально использует правильную формулировку.

## Минимальные operations

Реализованы:

- `trim`;
- `null_if`;
- `to_integer`;
- `lowercase`;
- `normalize_ip`;
- `validate_range`;
- `parse_timestamp`.

Cisco revision использует только необходимое:

- `pri`: integer + 0..191;
- `severity`: integer + 0..7;
- `src_port`, `dst_port`: integer + 0..65535;
- `hit_cnt`: integer;
- `src_ip`, `dst_ip`: IPv4;
- `timestamp`: `%b %d %Y %H:%M:%S` → naive ISO.

Остальные значения сохраняются без преобразований. Нули не превращаются в null. Split/project не реализованы в минимальной версии и могут быть добавлены как новая операция без изменения общей схемы.

## Хранение и инструменты

Добавлена таблица `normalization_revisions` с независимым номером revision и ссылкой на `parser_revision`.

Инструменты:

- `apply_normalization_operation`;
- `preview_normalization`;
- `evaluate_normalization`;
- `save_normalization`;
- `get_normalization`;
- `get_parser_research`.

Save gate требует описания всех полей record_schema и успешный parser+normalization на каждой evidence. Executor сам получает captures сохранённым parser, поэтому модель не может подменить вход.

## Независимая проверка

- Response `12` совпадает с сохранённой normalization revision 2.
- Исходный Response `8` найден и передан нормализатору.
- Все 4 evidence проходят parser и normalization.
- Все 1763 Cisco-строк имеют ровно один parser match.
- 30 записей, покрываемых `cisco_asa_acl_verdict@4`, нормализуются с `normalization_ok=true` и без ошибок.
- Остальные записи остаются parse-only: для их Families нормализация ещё не создана.

## Ограничения

- Нормализована одна Cisco Family, а не весь corpus.
- Глубина и истинность свободных описаний не имеют независимого golden-эталона.
- Timestamp остаётся naive ISO до получения timezone источника.
- Split одного capture в несколько значений отложен как отдельная extensible operation.
- Маппинг в ECS/OCSF, enrichment и классификация не выполняются.

## Артефакты

- `normalization.py` — operations, executor и evaluation.
- `normalization_agent.py` — ReAct нормализации.
- `runtime.py` — общий parser executor.
- `research/r027-cisco-normalization.db` — parser и две normalization revisions.
- `research/r027-cisco-normalization.summary.json` — машинная сводка.
- `research/r027-cisco-normalization.progress.jsonl` — research, repair и audit.

[← R026](026-symantec-kv-review.md) · [Все исследования](README.md)
