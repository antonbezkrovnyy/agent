# R021 — Juniper Junos на Registry R020

> Проверка переноса качества с Cisco на новый свободный текстовый формат. Все 26 строк Juniper обработаны: только 3 потребовали ReAct, остальные 23 покрыты сохранённым parser. Исходный R020 не изменялся.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R021` |
| Дата | 23 ноября 2026 г. |
| Baseline | Копия `research/r020-corpus.db` |
| Вход | `C:\Users\aa.bezkrovnyy\Downloads\opensecuritydatalake\parser-react\data\artifact_juniper.log` |
| Строк | 26 |
| Тип формата | Позиционный текст Junos `RT_FLOW_SESSION_DENY`, не KV/JSON/CEF/CSV |
| ReAct-исследования | 3 |
| Новая Family | `juniper_junos_rt_flow_session_deny` |
| Актуальная revision | `@6` |
| Итог | 26/26 покрыто, `failed=0` |

## Почему этот corpus

Juniper — первый новый источник после Cisco/Firepower. Строка содержит обычные позиционные элементы:

```text
RT_FLOW: RT_FLOW_SESSION_DENY: session denied
<src_ip>/<src_port>-><dst_ip>/<dst_port>
<service> <protocol>(<detail>)
<verdict> <src_vrf> <dst_vrf> UNKNOWN UNKNOWN <user> <interface>
```

Это позволило проверить не только повторное использование одинаковых строк, а построение новой грамматики с нуля под другой источник.

## Ход обработки

| Строка | Особенность | Результат |
|---:|---|---|
| 1 | Первая Junos-строка | Новая Family, revisions `@1–3` после quality corrections |
| 3 | `DENY_ALL`, `17(0)` | Расширение до `@4` |
| 11 | `junos-dns-udp`, `17(0)` | Расширение до `@6` |
| Остальные 23 | Те же грамматические формы | Покрыты без модели |

Итоговая `juniper_junos_rt_flow_session_deny@6` извлекает 20 captures, включая:

- PRI, timestamp и host;
- process и `msg_id`;
- source/destination IP и ports;
- service, protocol и detail;
- verdict и VRF;
- user и interface.

## Переиспользование Registry

Существующие Cisco/Firepower Families были проверены первой, но не совпали с Junos. Поэтому модель создала новую Family, а не ослабила Cisco-грамматику.

При этом базовые атомы оказались переиспользуемыми:

- syslog PRI;
- timestamp;
- IPv4;
- ports;
- host/interface-related fragments.

Итоговый Registry:

| Объект | Количество |
|---|---:|
| Family | 4 |
| Parser revisions | 18 |
| Grok | 64 |
| Research results | 13 |
| Semantic embeddings | 100 |

Из 64 Grok 15 добавлены или изменены в ходе Juniper-эксперимента и его аудита.

## Quality repair: global Grok ↔ Family

После corpus независимая проверка обнаружила `negative_example_matched:JUNOS_FLOW_SERVICE:'none'` в глобальном Registry.

Разбор показал:

- внутри latest Family `JUNOS_FLOW_SERVICE` имел корректные examples;
- глобальная запись содержала другие description и negative examples;
- определение regex совпадало, но metadata разошлась между сохранениями revisions.

### Решение

1. Глобальный `JUNOS_FLOW_SERVICE` синхронизирован с latest Family.
2. `save_parser_family` теперь отклоняет **различие definition** между Family и global Grok.
3. `check_response` выполняет ту же проверку перед принятием ответа.
4. Family-specific descriptions и examples разрешены, но обязаны проходить независимое исполнение. Требовать идентичную прозу для разных контекстов не нужно.

Отдельный negative test подтвердил блокировку `grok_registry_conflict` при различии definitions.

Это важное отличие от простого исправления примера: устранена возможность незаметного semantic drift библиотеки.

## Независимая проверка

Подтверждено:

- latest Juniper response проходит `check_response`;
- во всех трёх новых research responses нет source mismatches;
- все 64 глобальных Grok проходят examples и dependencies;
- `JUNOS_FLOW_SERVICE` больше не имеет конфликта `none`;
- все 26 строк покрываются ровно одной latest revision;
- конфликт определений при сохранении блокируется;
- `failed=0`.

Машинная сводка дополнена `semantic_embeddings=100` и событием `quality_repair`.

## Оценка

Эксперимент подтверждает перенос модели на новый текстовый источник:

- модель не привязана к Cisco;
- новая Family создаётся по фактическому mismatch;
- базовые Grok переиспользуются;
- quality gates продолжают работать на другом формате;
- финальный parser покрывает весь небольшой corpus без новых вызовов.

При этом 26 строк — небольшая выборка, а шесть revisions на три исследованных наблюдения показывают заметную стоимость промежуточных исправлений. Универсальность для других Junos message types не доказана.

## Ограничения

- Проверен только `RT_FLOW_SESSION_DENY`, а не Junos в целом.
- Нет golden-эталона правильности всех captures.
- Свободные описания по-прежнему требуют отдельной смысловой проверки.
- Для Juniper не потребовались специальные межполевые constraints, поэтому их отсутствие в revision не является ошибкой.
- Нормализация не исследовалась: задача — только парсинг.

## Вывод

**R021 подтверждает работоспособность системы на новом источнике.** Главный результат не только 26/26, а выявление и устранение drift между глобальной Grok-библиотекой и Family. Latest revisions, source paths, examples и definition consistency теперь проверяются независимо от модели.

## Артефакты

- `research/r021-juniper.db` — Registry с новой Family.
- `research/r021-juniper.summary.json` — машинная сводка.
- `research/r021-juniper.progress.jsonl` — corpus и `quality_repair`.
- `research/021-juniper-review.md` — настоящий отчёт.

[← R020](020-quality-gates-review.md) · [Все исследования](README.md)
