# R016 — один prompt для независимых запусков

> Две разные RAW-строки обработаны двумя независимыми процессами с одним `RESEARCH_PROMPT`, без истории диалога и без специальных инструкций для первой или второй строки. Состояние передаётся только через SQLite.

## Карточка эксперимента

| Параметр | Значение |
|---|---|
| ID | `R016` |
| Дата | 23 ноября 2026 г. |
| Модель | `deepseek-flash`, temperature 0 |
| Вход | Две строки Cisco ASA, каждая аргументом отдельного запуска |
| Чтение файла логов | Не выполняется |
| Prompt | Один `RESEARCH_PROMPT` для обоих запусков |
| Хранилище | `research/r016-unified-prompt.db` |
| Structured output | `Response.model_validate_json` внутри ReAct-контура |
| Итог | Две Family, 6 Grok-конструкций, 2 проверенных research-результата |
| Статус | Независимые запуски работают; обе Family исполняются с пустым residual |

## Исследовательский вопрос

Может ли второй запуск без предыдущего диалога и специального prompt:

1. обнаружить сохранённую Family;
2. исполнить её на новой строке;
3. отклонить неподходящую грамматику;
4. переиспользовать конструкции;
5. создать новую Family;
6. проверить, что прежняя Family не сломана?

**Результат: да, при детерминированной проверке финального ответа runner-ом.**

## Единый сценарий

Из кода удалены `INITIAL_PROMPT`, `SECOND_PROMPT`, `RESUME_FIRST` и `LEARN_SECOND`. Запуск принимает одну строку:

```bash
RESEARCH_DB=research/r016-unified-prompt.db uv run python agent.py '<RAW-строка>'
```

Каждый запуск выполняет один и тот же путь:

```text
RAW
→ list_parser_families + search_grok_patterns
→ get_parser_family
→ исполнение существующих parser на RAW
→ reuse / extend_layer / new_variant / new_family / unknown
→ execute_grok_parser + validate_grok_result
→ evaluate_candidate с независимым baseline
→ сохранение Grok и Family
→ независимая проверка Response runner-ом
→ сохранение research JSON
```

Для интерактивного ввода действует то же правило: каждая введённая строка является новым независимым наблюдением, а не продолжением предыдущего исследования.

## Первый запуск

База была пуста. Модель выбрала `new_family` и сохранила:

```text
Family: asa_106100_acl_hit@1
Grok: ASATIMESTAMPYEAR, ASATAG, ASAPAYLOAD, HEXREF
```

Слои:

```text
syslog_envelope → asa_tag → asa_106100_body
```

Результат независимого исполнения:

| Проверка | Результат |
|---|---|
| Полное совпадение | `ok=true` |
| Captures | 20, включая промежуточные `payload` и `body` |
| Обязательные поля | Все получены |
| Residual | Пуст |
| Family в SQLite | Сохранена и получена повторно |

## Второй независимый запуск

Второй процесс получил только новую RAW-строку, тот же prompt и общую БД. Он обнаружил `asa_106100_acl_hit@1`, загрузил её и исполнил без изменений.

Baseline не совпал уже на `syslog_envelope`:

- во второй строке отсутствует host;
- после timestamp используется другой разделитель;
- `msg_id=106023` вместо `106100`;
- тело имеет форму `src iface:ip/port dst iface:ip/port`, а не форму первой Family.

Модель выбрала `new_family`, а не ослабление существующего parser.

### Переиспользованы

```text
ASATIMESTAMPYEAR
ASATAG
ASAPAYLOAD
HEXREF
```

### Добавлены

```text
ASAIFACE
ASAQSTRING
```

### Создана Family

```text
asa_106023_acl_deny@1
syslog_envelope → asa_tag → asa_106023_body
```

`evaluate_candidate` подтвердил candidate на второй строке и независимый baseline на первой.

## Найденные ошибки и исправления контура

| Наблюдение | Исправление |
|---|---|
| Модель передала неверные аргументы инструмента | LangChain вернула конкретную ошибку параметра; модель исправила вызов |
| Финальный ответ пришёл обычным текстом вместо JSON | Pydantic-ошибка возвращается модели в том же диалоге; исследование не повторяется |
| `ToolStrategy(Response)` потребовал неподдерживаемый `tool_choice` | Провайдер вернул `Thinking mode does not support this tool_choice`; подход отклонён |
| JSON был валидным, но parser не был исполнен и сохранён | Добавлена обязательная независимая проверка parser и SQLite перед завершением |
| Сохранение research JSON зависело от вызова инструмента моделью | Финальный валидный Response сохраняет runner |

Финальные ошибки обрабатываются единообразно с ошибками инструментов: модель получает `invalid_arguments`, `parser_validation_failed` или `parser_not_persisted` с конкретным действием для исправления. Процесс не принимает непроверенный результат.

## Итоговая регрессия из SQLite

Оба parser были получены из БД и исполнены независимо от модели.

| Family | Вход | Captures | Residual | Research validation |
|---|---|---:|---|---|
| `asa_106100_acl_hit@1` | Первая строка | 20 | Пуст | `check_response=None` |
| `asa_106023_acl_deny@1` | Вторая строка | 17 | Пуст | `check_response=None` |

Проверка «текст вместо JSON → ошибка → исправлённый JSON» также пройдена: второй валидный ответ принят без повторного исследования.

## Состояние хранилища

| Объект | Количество |
|---|---:|
| Grok-конструкции | 6 |
| Parser families | 2 |
| Research results | 2 |

```text
asa_106100_acl_hit@1
asa_106023_acl_deny@1
```

## Изменённая структура решения

| Файл | Ответственность |
|---|---|
| `prompts.py` | Единый prompt и требование JSON |
| `schemas.py` | Описанные Pydantic-структуры |
| `tools.py` | Типизированные инструменты и ошибки вызовов |
| `grok.py` | Grok compiler, исполнение и регрессия |
| `storage.py` | SQLite Registry |
| `validation.py` | Независимая проверка финального Response |
| `agent.py` | ReAct, независимые запуски и сохранение research JSON |

## Ограничения

- Проверены только две ASA-строки; универсальность для других источников не доказана.
- Повторная одинаковая строка и сценарий `reuse` отдельно не исследовались.
- `extend_layer` и `new_variant` не проверены на реальных наблюдениях.
- Нет регрессии полного corpus и отдельного production dispatch всех Family.
- Нет жизненного цикла `candidate → validated → published`.
- Семантика hex-полей остаётся гипотезой; независимая проверка подтверждает разбор, а не смысл этих значений.

## Вывод

**R016 — текущий системный baseline.** Одна и та же модельная инструкция работает в отдельных процессах, а накопленное состояние доступно через SQLite. Второй запуск не требует истории или hardcoded family_id и способен создать новую Family с регрессией прежней.

При этом надёжность обеспечивается не только prompt: runner отклоняет валидный по форме, но неисполненный или несохранённый parser.

## Артефакты

- `research/r016-unified-prompt.db` — итоговый SQLite Registry.
- `research/016-first-run.json` — structured response первой строки без reasoning.
- `research/016-second-run.json` — structured response второй строки без reasoning.
- `research/016-unified-independent-runs-review.md` — настоящий отчёт.
- `prompts.py` — точный единый `RESEARCH_PROMPT`.

[← R015](015-second-family-review.md) · [Все исследования](README.md)
