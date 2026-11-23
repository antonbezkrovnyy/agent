# R014 — Parser Registry в SQLite

> Переход от разового ответа модели к системе: Grok-конструкции, parser families и research results сохраняются и получаются из постоянной базы данных.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R014` |
| Дата | 23 ноября 2026 г. |
| Вход | Первая строка Cisco ASA |
| Хранилище | `research/r014e.db` (SQLite) |
| Structured output | Один ReAct-запуск, `Response.model_validate_json` |
| Дополнительный вызов модели для JSON | Не используется |
| Статус | Сохранение, получение и исполнение из БД успешны |

## Краткий результат

**Модель создала и сохранила 6 пользовательских Grok-конструкций и parser family. Загруженный из SQLite парсер независимо выполнен на исходной строке: полное совпадение, residual пуст.**

| Объект | Количество |
|---|---|
| Пользовательские Grok-конструкции | 6 |
| Слои parser family | 3 |
| Parser families | 1 (`syslog_acl_verdict`, revision 1) |
| Research results | 2 версии результата |
| Поля `record_schema` | 18 |
| Независимое исполнение из БД | Успешно |

## Инструменты хранилища

| Инструмент | Назначение |
|---|---|
| `save_grok_pattern` | Сохранение именованной Grok-конструкции без перезаписи другого определения |
| `get_grok_pattern` | Получение конструкции по имени |
| `search_grok_patterns` | Поиск по имени, определению и описанию |
| `save_parser_family` | Сохранение ревизии, слоёв, схемы и Evidence |
| `get_parser_family` | Получение последней ревизии Family |
| `save_research_result` | Сохранение полного structured response |

Сохранение Grok и Family идемпотентно: повтор с тем же содержимым возвращает `unchanged`, конфликт содержимого — явную ошибку вместо молчаливой перезаписи.

## Сохранённые конструкции

```text
HIT_PHASE
HIT_TAIL
IFACE_DST_ADDR_PORT
IFACE_SRC_ADDR_PORT
MSGID_TOKEN
SYSLOGTIMEWITHYEAR
```

Слои Family:

```text
envelope_message_id
→ payload_acl_verdict
→ hit_counter_tail
```

Пример `SYSLOGTIMEWITHYEAR`:

```text
%{MONTH} +%{MONTHDAY} %{YEAR} %{TIME}
```

## Проверка из базы данных

После запуска парсер был получен не из ответа модели, а из `parser_families` и выполнен независимо. Полученные captures включают:

```text
pri=132
timestamp=Feb 12 2023 13:00:30
host=10.11.200.67
mnemonic=ASA
severity=4
msg_id=106100
acl_name=CSM_FW_ACL_
action=denied
protocol=tcp
src_interface=Outside
src_ip=131.9.85.43
src_port=14015
dst_interface=PUB_DMZ
dst_ip=191.189.210.52
dst_port=22
hit_cnt=1
hit_phase=first hit
hit_tracker=0x6921d058, 0x00000000
```

Итог:

```json
{
  "ok": true,
  "error": null,
  "residual": ""
}
```

## Исправления контура

- Убран второй модельный вызов для structured output: ReAct сам заканчивается JSON, код валидирует его через `Response.model_validate_json`.
- Из консоли удалены сырые `tool_call_chunks`; остаются только завершённые вызовы и результаты инструментов.
- При неверных аргументах инструменты возвращают `invalid_arguments`, объяснение и пример правильного вызова вместо `KeyError`.
- `evaluate_candidate` проверяет baseline и candidate независимо: разные Family не обязаны совпадать друг с другом.

## Оценка

| Критерий | Результат |
|---|---|
| Сохранение Grok в SQLite | Работает |
| Получение Grok из SQLite | Работает |
| Сохранение parser family | Работает |
| Получение последней ревизии | Работает |
| Исполнение из базы | Полное совпадение |
| Structured output без второго вызова | Работает |
| Обработка неверного использования инструментов | Возвращает объяснение |
| Reasoning в артефактах | Не сохраняется |

## Вывод

**R014 — первый работающий системный baseline:** модель создаёт конструкции, код проверяет их, а SQLite хранит версии для последующего получения и переиспользования. Теперь новая строка может начинаться не с пустого prompt, а с `search_grok_patterns` и `get_parser_family`.

## Артефакты

- `research/r014e.db` — SQLite Registry.
- `research/014-sqlite-backed-parser.json` — финальный structured response без reasoning.
- `research/014-sqlite-backed-parser-review.md` — настоящий отчёт.
- `agent.py` — CRUD, Grok compiler и однопроходный ReAct.

[← R012](012-executed-grok-parser-review.md) · [Все исследования](README.md)
