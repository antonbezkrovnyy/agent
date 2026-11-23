# R015 — вторая Family через SQLite Registry

> Обработка ранее неизвестной строки 21 с получением baseline из базы, выбором `new_family`, сохранением конструкций и независимой регрессией.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R015` |
| Дата | 23 ноября 2026 г. |
| Baseline | `syslog_acl_verdict@1` (`ASA-106100`) |
| Новая строка | Строка 21 (`ASA-106023`) |
| Хранилище | `research/r014e.db` |
| Решение | `new_family` |
| Новая Family | `syslog_acl_deny_by_group@1` |
| Статус | Обе Family исполняются, baseline не изменён |

## Ожидаемый и фактический результат

| Ожидание | Факт |
|---|---|
| Получить baseline из SQLite | Выполнено |
| Не расширять `106100` под другую запись | Сохранена без изменений |
| Определить новую Family | Создана `syslog_acl_deny_by_group` |
| Переиспользовать конструкции | `SYSLOGTIMEWITHYEAR`, `MSGID_TOKEN` |
| Сохранить новую ревизию | Revision 1 сохранена |
| Проверить обе строки | Обе успешны, residual пуст |
| Получить новую Family из БД | Получена и независимо исполнена |

## Решение модели

Строка 21 не совпала с baseline уже на конверте:

- отсутствует host;
- после timestamp используется другой разделитель;
- `msg_id=106023` вместо `106100`;
- полностью отличается грамматика тела.

Поэтому `extend_layer` неприменим. Создана новая Family, а не ослаблены правила `106100`.

## Переиспользование и новые конструкции

### Переиспользованы из R014

```text
SYSLOGTIMEWITHYEAR
MSGID_TOKEN
```

### Созданы для новой Family

```text
ENDPOINT_SRC_SLASH
ENDPOINT_DST_SLASH
ACCESSGROUP_CLAUSE
TRACKER_BLOCK
```

Слои новой Family:

```text
envelope_message_id
→ payload_deny_by_group
→ tracker_split
```

## Регрессия

| Проверка | Результат |
|---|---|
| Baseline на строке 1 | `ok=true`, residual пуст |
| Candidate на строке 21 | `ok=true`, residual пуст |
| Required captures | Все получены |
| Старая Family перезаписана | Нет |

Captures новой Family из SQLite:

```text
pri=132
timestamp=Feb 12 2023 13:21:55
mnemonic=ASA
severity=4
msg_id=106023
action=Deny
protocol=tcp
src_interface=VQE#2-Lab
src_ip=13.253.52.235
src_port=55896
dst_interface=outside
dst_ip=77.53.224.176
dst_port=25000
acl_name=VQE#2-Lab
tracker_block=[0x0, 0x0]
hit_tracker=0x0, 0x0
```

## Состояние хранилища

| Объект | Количество |
|---|---|
| Grok-конструкции | 10 |
| Parser families | 2 |
| Research results | 3 |

Семьи в БД:

```text
syslog_acl_verdict@1
syslog_acl_deny_by_group@1
```

## Оценка

**R015 подтверждает работу системного подхода:** новая неизвестная строка начинается с сохранённого baseline, ведёт к созданию новой Family при необходимости, а библиотека переиспользует атомы вместо переписывания всего парсера. Регрессия доказывает, что расширение библиотеки не сломало прежнюю Family.

Оставшиеся ограничения относятся к содержанию: смысл hex-блока и обязательность портов для других протоколов остаются гипотезами, а не доказанными свойствами Family.

## Артефакты

- `research/r014e.db` — обновлённый SQLite Registry.
- `research/015-second-family.json` — structured response без reasoning.
- `research/015-second-family-review.md` — настоящий отчёт.

[← R014](014-sqlite-backed-parser-review.md) · [Все исследования](README.md)
