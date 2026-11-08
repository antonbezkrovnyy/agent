# LogiCore MCP

Read-only MCP-сервер для поиска и анализа событий LogiCore через MARS API.
Предназначен для LLM-агентов: инструменты имеют типизированные входные схемы,
возвращают фактический IQL, явные ограничения результата и подсказки следующего действия.

## Архитектура и границы

```text
LLM agent → MCP logicore → MARS /mars/api/v1 → Earth → ClickHouse
```

- MCP принимает готовый IQL и передаёт его в `data.query.filters` без JsonLogic
  и собственной трансляции.
- Преобразование IQL в ClickHouse SQL выполняет Earth. Итоговый SQL в ответ MARS
  не входит, поэтому MCP его не возвращает.
- Реализованы только read-only сценарии. Запуск/отмена синхронизации, изменение
  настроек и произвольный SQL-режим не предоставляются.
- MARS `groups` поддерживается как дополнительные равенства, а не как `GROUP BY`.
- Потоковый `/events/export` не используется: `extract_data` выполняет ограниченную
  выгрузку постранично через `/events`.

## Запуск

Из корня репозитория:

```bash
cp .env.example .env
# Заполнить LOGIQ_URL, LOGIQ_USER, LOGIQ_PASSWORD, LOGIQ_API_TOKEN

docker compose up -d --build logicore-mcp
docker compose ps logicore-mcp
docker compose exec -T logicore-mcp python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9000/health').read().decode())"
```

Ожидаемый health response:

```json
{"ok": true, "transport": "streamable-http"}
```

MCP endpoint находится только во внутренней Docker-сети:

```text
http://logicore-mcp:9000/mcp
Authorization: Bearer <LOGIQ_API_TOKEN>
```

Порт 9000 намеренно не публикуется наружу. `/health` доступен без токена только
для healthcheck контейнера. Остановка после проверки:

```bash
docker compose stop logicore-mcp
```

### Обязательная конфигурация

| Переменная | Назначение |
|---|---|
| `LOGIQ_URL` | Базовый URL стенда, например `http://<stand-ip>:5555` |
| `LOGIQ_USER` | Учётная запись с чтением событий и advanced IQL |
| `LOGIQ_PASSWORD` | Пароль учётной записи |
| `LOGIQ_API_TOKEN` | Bearer-токен доступа к MCP |
| `LOGIQ_TLS_VERIFY` | Проверка TLS: `true`/`false` |

В Compose дополнительно заданы `LOGIQ_TRANSPORT=streamable-http`,
`LOGIQ_AUTH=token` и `LOGIQ_HOST=0.0.0.0`.

### Необязательные настройки

| Переменная | Default | Назначение |
|---|---:|---|
| `LOGIQ_PORT` | 9000 | Порт MCP |
| `LOGIQ_TENANT_IDS` | все доступные | Тенанты через запятую; пересекаются с правами учётной записи |
| `LOGIQ_DEFAULT_COLUMNS` | см. `config.py` | Проекция событий через запятую |
| `LOGIQ_TIMEOUT_CONNECT` | 5 | Connect timeout Earth, секунды |
| `LOGIQ_TIMEOUT_READ` | 30 | Read timeout Earth, секунды |
| `LOGIQ_WS_WAIT` | 30 | Таймаут SSE timeline, секунды |
| `LOGIQ_EARTH_CONCURRENCY` | 1 | Число одновременных обращений к MARS |
| `LOGIQ_SPLIT_MIN_SPAN_S` | 3600 | Минимальное окно при адаптивном дроблении |
| `LOGIQ_SPLIT_BUDGET_S` | 600 | Общий бюджет дробления, секунды |
| `LOGIQ_EXTRACT_MAX_ROWS` | 1000 | `max_rows` выгрузки по умолчанию |
| `LOGIQ_RATE_LIMIT_CALLS` | 300 | Вызовов MCP за окно rate limit; 0 отключает |
| `LOGIQ_RATE_LIMIT_WINDOW_S` | 600 | Размер скользящего окна rate limit, секунды |
| `LOGIQ_LOG_FILE` | пусто | Дополнительный файл журнала; основной журнал — stderr |

Для локального stdio-запуска нужен Python 3.11 и зависимости из `Dockerfile`:

```bash
LOGIQ_TRANSPORT=stdio LOGIQ_URL=... LOGIQ_USER=... LOGIQ_PASSWORD=... \
  python -m logicore_mcp
```

`streamable-http` с `LOGIQ_AUTH=none` не запускается. OAuth присутствует в коде,
но для передаваемого сценария используется статический Bearer; multiuser/OIDC не
является целевым режимом.

## Инструменты

| Инструмент | Назначение и специфические параметры |
|---|---|
| `search_events` | События: `iql`, `columns`, `page`, `limit`, `order`, `groups`, `order_by`, `query_limit`, `include_activity` |
| `extract_data` | Постраничная выгрузка: те же параметры поиска плюс `max_rows`; `include_activity` отсутствует |
| `count_events` | Точный count и события/минуту: `iql`, `groups`, `query_limit` |
| `aggregate_events` | Настоящий GROUP BY до 5 `dimensions`, count групп, `complete`, `groups_lower_bound` |
| `timeline` | Ровные бакеты по времени: `iql`, `groups` |
| `top_values` | Top/bottom одного поля: `field`, `iql`, `limit` |
| `top_values_batch` | До 10 полей за вызов, сбой одного поля не отменяет остальные |
| `get_event` | Полная карточка по `uuid + timestamp` из одной строки `search_events` |
| `field_fill_rates` | Заполненность всех полей с курсором `offset`, `failed` и `time_coverage` |
| `duplicates` | Группы повторов по `key_fields`, `limit` |
| `search_fields` | Поиск имён и описаний в живой схеме без обращения к событиям |
| `field_lookup` | До 20 независимых результатов; неизвестное имя не скрывает валидные поля |
| `enum_lookup` | Значения и расшифровки перечислений таксономии MP SIEM 7.0 |
| `source_info` | Endpoint, tenant IDs, SHA-256 схемы и `fake_earth` без секретов |
| `server_time` | Локальное время MCP-процесса для вычисления относительных окон |

Resource `logicore://schema/fields` содержит фактические имена и типы полей.
Схема кэшируется до перезапуска процесса; после изменения схемы MARS перезапустите
`logicore-mcp`.

## IQL и нативные параметры MARS

`iql` — одно WHERE-условие для `data.query.filters`, не полный `SELECT` и не
несколько statements. MCP не парсит выражение: ошибки MARS возвращаются в `error/hint`.

Простой пример:

```json
{
  "time_from": "2026-11-04T20:00:00Z",
  "time_to": "2026-11-04T20:10:00Z",
  "iql": "action = 'login' AND status IS NOT NULL",
  "columns": ["uuid", "timestamp", "action", "message"],
  "limit": 20,
  "order": "desc"
}
```

Функции ClickHouse передаются напрямую:

```json
{
  "time_from": "2026-11-04T20:00:00Z",
  "time_to": "2026-11-04T20:10:00Z",
  "iql": "positionCaseInsensitive(message, 'error') > 0 AND has(tags, 'unparsed')",
  "limit": 20
}
```

Нативные `groups`, полная сортировка и общий лимит:

```json
{
  "time_from": "2026-11-04T20:00:00Z",
  "time_to": "2026-11-04T20:10:00Z",
  "groups": [{"column": "action", "value": "login"}],
  "order_by": [
    {"column": "timestamp", "action": "DESC"},
    {"column": "uuid", "action": "DESC"}
  ],
  "query_limit": 100,
  "limit": 20
}
```

- `groups` объединяются по AND; `value` может быть строкой, списком строк или `null`.
- `order_by` заменяет простой `order`. Без него применяется стабильный порядок
  `timestamp + uuid` в направлении `order`.
- `limit` в `search_events` — размер страницы, `query_limit` — потолок всего запроса.
  При достижении `query_limit`: `complete=false`, у search дополнительно
  `has_more=false` и `next_page=null`.
- `count_events` при `count >= query_limit` тоже возвращает `complete=false`:
  это не полное число событий окна.

## Контракт результата

Общие свойства:

- `ok` — успешен ли предметный вызов;
- `iql` — фактически выполненная строка фильтра или `null`;
- `filter_applied` — эхо того же фильтра для совместимости там, где оно предусмотрено;
- `window` — фактические границы и источник `explicit`/`to-now`;
- `duration_s` — полная длительность вызова, включая очередь и дробление;
- `hint` — следующее действие или объяснение ограничения.

### Полнота и пагинация

- `complete=false` означает, что полнота не доказана или данные частичны.
- `coverage.complete=false` и `time_coverage` описывают покрытую часть окна.
- `has_more=true` выставляется только после look-ahead-запроса, вернувшего строки
  следующей страницы. Пустая следующая страница даёт `has_more=false`, `next_page=null`.
  Если проверка продолжения не удалась, `has_more=null` и есть `pagination_error`;
  это неизвестность, а не подтверждённое продолжение. `next_page` есть только при true.
- `truncated=true` означает усечение или неизвестную полноту; общий `total` тогда не
  выдаётся. Точный объём получают через `count_events` без `query_limit`.
- Пагинация стабилизирована по `timestamp + uuid`.
- Повтор страницы при выгрузке останавливает цикл с `complete=false`, `next_page=null`
  и машинным `pagination_error=duplicate_page`. Уже прочитанные события остаются в ответе
  с `ok=true`: это пригодный partial-результат, а не failed-запрос; подробность остаётся в `hint`.

### Группировки и справочники

- `aggregate_events` использует `/events/groups` с составным `columns`; `groups` из
  search/count остаётся дополнительными equality-фильтрами, это другой параметр MARS.
  `groups_total` — точный COUNT DISTINCT только при `complete=true`; при потолке или
  частичном покрытии он `null`, а `groups_lower_bound` — лишь прочитанное число групп.
  Тогда нужно сузить окно/фильтр либо выполнить SQL над полной выгрузкой.
- `distinct` при `complete=false` — нижняя граница, а не итог окна.
- `share` при усечении — процент от суммы прочитанных групп, не всех событий.
- `null_count` в `top_values` — число null-групп, не null-событий.
- `duplicate_groups_total` при `ceiling_reached=true` — только число прочитанных
  групп; для полного результата сузьте окно/фильтр.
- Пустая выдача без признака потолка не означает ошибку; отсутствие значения в
  top-выдаче не доказывает его отсутствие в середине распределения.

### `field_fill_rates`

- `offset` — курсор следующей страницы, не смещение события.
- Страницы не кумулятивны: объединяйте `filled`, `empty`, `failed` всех страниц
  с одинаковым окном и IQL.
- `complete` относится к курсору полей, а не к отсутствию ошибок.
- `failed` содержит поля, которые надо повторить или проверить точечно.
- `offset == fields_total` — корректный конец; больший offset — ошибка аргумента.

### Ошибки

Предметная ошибка возвращается как JSON:

```json
{
  "ok": false,
  "error": "MARS отклонил SQL/IQL-выражение",
  "hint": "Проверьте iql: это одно WHERE-условие без SELECT и ';'. Исправьте синтаксис и повторите.",
  "duration_s": 0.1
}
```

Ошибки типов и диапазонов аргументов проходят через штатный MCP `isError`.
Неожиданные исключения также превращаются в конверт с классом ошибки и hint;
трассировка остаётся в stderr. `asyncio.CancelledError` не маскируется.

## Лимиты

| Ограничение | Значение |
|---|---:|
| Строк на страницу `search_events` | 1–100, default 20 |
| Значений top/bottom | 1–20, default 5 |
| Полей в `top_values_batch` | 1–10 |
| Колонок в проекции | до 20 |
| Групп дубликатов | 1–50, default 10 |
| Полей в ключе дубликата | до 5 |
| Групп в распределении | до 100 |
| Моментов в `activity` | до 20 |
| Строк на страницу `extract_data` | 100 |
| `max_rows` одного вызова | 1–5000 |
| Окно `timeline` | до 8 дней |
| `field_lookup` | до 20 полей |
| MCP rate limit | 300 вызовов за 600 секунд |

При таймаутах MARS оконные запросы дробятся последовательно. Результат может быть
частичным и всегда содержит соответствующие флаги; не используйте его как полный.
Если SSE timeline закрылся без `done`, ответ также будет `complete=false`.

## Безопасность

- Все инструменты имеют `readOnlyHint=true`, `destructiveHint=false`,
  `idempotentHint=true`, `openWorldHint=true`.
- IQL не ограничивается собственным whitelist MCP. Используйте выделенную учётную
  запись с минимально необходимыми правами и ограниченными тенантами.
- Не передавайте в IQL полный SQL или statements; принимается только условие.
- Bearer и пароль Earth берутся из env и не включаются в ответы MCP.
- `/health` — единственный неавторизованный маршрут.
- Для production требуется отдельное решение о сетевом доступе, TLS и multiuser;
  текущий режим рассчитан на внутреннюю Docker-сеть.

## Приёмка

Из корня репозитория:

```bash
uv run --with "langchain==1.3.10" --with "langgraph==1.2.16" \
  --with "langchain-openai" --with "pydantic-settings" \
  --with "httpx" --with "tenacity" --with "pytest" --with "loguru" \
  --with "psycopg" --with "mcp[cli]>=2,<3" \
  --with "fastapi" --with "uvicorn" pytest -q tests

git diff --check
```

Минимальный живой сценарий после `docker compose up -d --build logicore-mcp`:

```bash
docker compose exec -T logicore-mcp python - <<'PY'
import asyncio
import json
from datetime import UTC, datetime, timedelta
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from logicore_mcp.config import load_settings

async def main():
    settings = load_settings()
    headers = {"Authorization": f"Bearer {settings.api_token}"}
    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as http:
        async with streamable_http_client("http://127.0.0.1:9000/mcp", http_client=http) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=120.0) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 12
                now = datetime.now(UTC).replace(microsecond=0)
                result = await session.call_tool("count_events", {
                    "time_from": (now - timedelta(minutes=10)).isoformat(),
                    "time_to": now.isoformat(),
                    "iql": "positionCaseInsensitive(message, 'cpu') >= 0",
                })
                payload = json.loads(result.content[0].text)
                assert payload["ok"] is True, payload
                assert payload["iql"] == "positionCaseInsensitive(message, 'cpu') >= 0"
                print(json.dumps({"tools": len(tools.tools), "count": payload["count"], "ok": payload["ok"]}))

asyncio.run(main())
PY
```

Дополнительно проверить на стенде:

1. `search_events` без IQL и с нативной функцией IQL.
2. `search_events` и `extract_data` с `groups`, `order_by`, `query_limit`.
3. `get_event` по паре `uuid + timestamp` из реальной строки поиска.
4. `timeline`, `top_values`, `top_values_batch`, `duplicates`.
5. `field_fill_rates(offset=fields_total)` и ошибку при большем offset.
6. Невалидный IQL: ожидается `ok=false` с понятным `hint`.
7. `docker compose stop logicore-mcp` после проверки.

## Карта кода

```text
logicore_mcp/
├── config.py                 # env, auth, проекция, лимиты и тенанты
├── mcp/
│   ├── contracts.py          # Pydantic-контракты аргументов
│   ├── tools.py              # публичные MCP tools и docstrings
│   ├── flows.py              # валидация, вызовы и конверты результатов
│   ├── extract.py            # детерминированная пагинация выгрузки
│   ├── prompts.py            # готовые MCP prompts
│   ├── runtime.py            # singleton client и кэш схемы
│   └── server.py             # stdio/streamable-http, health, auth
├── earth/
│   ├── client.py             # MARS endpoints, login и ошибки
│   ├── schemas.py            # нативные тела IQL-запросов
│   ├── scheduler.py          # очередь и адаптивное дробление окон
│   └── timeline_sse.py       # SSE init/chunk/done/error
└── domain/
    ├── filters.py            # схема полей и похожие имена
    ├── projection.py         # проекции и признаки полноты
    └── merge.py              # слияние результатов дробления
```

Тесты интеграции MCP: `tests/test_mcp_contracts.py`, `tests/test_extract_data.py`,
`tests/test_timeline_sse.py`. Общая архитектура агента — `docs/DEV.md`.
