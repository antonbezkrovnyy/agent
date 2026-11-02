# LogiCore Agent

Многоагентная система для расследования событий LogiCore. LangGraph управляет
диалогом и расследованием, MCP-сервер читает события через Mars API, Aegra
предоставляет API и хранение состояния, Phoenix собирает трассировку.

## Что умеет

- отвечать на обычные вопросы без обращения к LogiCore;
- фиксировать временное окно расследования и сохранять его в follow-up;
- искать, считать и агрегировать события через read-only MCP-инструменты;
- сохранять фактические вызовы и результаты как `Evidence`;
- проверять доказательства и отчёт структурированными критиками;
- выгружать полную таблицу и Markdown-файл без генерации строк через LLM;
- архивировать отчёт перед подтверждённой сменой расследования.

## Сервисы

| Сервис | Порт | Назначение |
|---|---:|---|
| `aegra` | 2026 | LangGraph/Aegra API, workers и персистентное состояние |
| `agent-chat-ui` | 3000 | Веб-чат на Next.js |
| `logicore-mcp` | 9000, только Docker-сеть | 12 read-only MCP-инструментов LogiCore |
| `anomaly-relay` | 2025 | Webhook LogiCore → тред и run в Aegra |
| `postgres` | 5432 | Состояние Aegra и checkpointer |
| `redis` | 6379 | Очередь задач и SSE-брокер |
| `phoenix` | 6006 | OpenInference/OpenTelemetry-трассировка |

## Запуск

Требуется Docker с Compose v2:

```bash
cp .env.example .env
# Заполнить .env
docker compose up -d --build
```

Основные переменные:

- `LLM_BASE_URL`, `LLM_TOKEN`, `LLM_MODEL` — OpenAI-совместимый LLM-провайдер;
- `LOGIQ_URL`, `LOGIQ_USER`, `LOGIQ_PASSWORD`, `LOGIQ_TLS_VERIFY` — стенд LogiCore;
- `LOGIQ_API_TOKEN` — авторизация MCP streamable-http;
- `POSTGRES_PASSWORD` — пароль Postgres;
- `RELAY_TOKEN`, `DEFAULT_CALLBACK_URL` — опциональная доставка webhook/отчётов.

После запуска:

- чат: http://localhost:3000;
- API: http://localhost:2026;
- Phoenix: http://localhost:6006;
- webhook аномалий: `http://<хост>:2025/anomaly`.

Проверка состояния:

```bash
docker compose ps
curl http://localhost:2026/health
```

## Граф работы

```mermaid
flowchart TD
    START([START]) --> entry --> intent
    intent -->|simple| chat --> END([END])
    intent -->|export_data| export --> END
    intent -->|новые данные| scout
    intent -->|continuation| investigator
    intent -->|ответ или правка отчёта| reporter
    intent -->|new/uncertain + история| confirm
    confirm -->|продолжить| scout
    confirm -->|новое здесь/новый чат| archive
    archive -->|новое здесь| scout
    archive -->|новый чат| END
    scout --> investigator --> critic_evidence
    critic_evidence -->|ok или лимит rework| reporter
    critic_evidence -->|rework_data| investigator
    reporter --> critic_report
    critic_report -->|rework_report| reporter
    critic_report -->|rework_data| investigator
    critic_report -->|ok или лимит rework| END
```

При `CRITIC_ENABLED=false` критики пропускаются. Узел `export` не вызывает LLM
и не проходит критиков: он механически выгружает текущий фильтр и форматирует
результат.

Полная схема маршрутизации и состояния — в [DEV.md](DEV.md).

## Лимиты и таймауты

Пользовательского бюджета времени и вопросов о нём нет.

| Ограничение | Значение |
|---|---:|
| Tool calls скаута за один `ainvoke` | 10 |
| Tool calls инвестигейтора за один `ainvoke` | 30 |
| Tool calls репортёра за один `ainvoke` | 60 |
| Таймаут одного вложенного `create_agent.ainvoke` | 600 с |
| Таймаут узла `export` | 120 с |
| Возвраты на доработку за внешний run | 1 |
| Строк на страницу `extract_data` | 100 |
| Строк в `extract_data` по умолчанию | 1000 (`LOGIQ_EXTRACT_MAX_ROWS`) |
| Допустимый `max_rows` одного вызова | 1–5000 |
| MCP rate limit | 300 вызовов за скользящие 600 с |

`run_limit` сбрасывается при каждом новом запуске соответствующего агента:
новое сообщение, повтор после доработки или повторная попытка получения
структурированного ответа. Это не общий лимит чата.

При исчерпании tool limit middleware завершает текущий tool-цикл, а внешний
граф продолжает работу. При таймауте агент возвращает структурированное
ограничение; таймаут не является гарантией длительности всего run. Прямые
вызовы `intent`, `chat` и критиков отдельным таймаутом сейчас не ограничены.

Текущий этап и счётчик вызовов отображаются одной карточкой `progress`, без
публикации внутренних tool-сообщений. Счётчик обновляется после каждого tool call.
После ответа UI может предложить быстрые
действия: полную таблицу, сохранение отчёта или продолжение расследования.

## Структура проекта

```text
├── agent/
│   ├── graph.py           # внешний StateGraph и маршруты
│   ├── agents.py          # runtime scout/investigator/reporter
│   ├── investigation.py   # intent, окно, архив и подтверждение
│   ├── export.py          # детерминированная таблица и файл
│   ├── critics.py         # критики доказательств и отчёта
│   ├── progress.py        # статус, лимиты и suggestion-действия
│   ├── llm.py             # LLM и Pydantic-схемы ответов
│   ├── state.py           # внешнее и внутреннее состояние
│   ├── tools.py           # MCP tools, Evidence, отчёты
│   └── prompts/           # системные промпты
├── logicore-mcp/          # MCP-сервер и пагинация Mars API
├── anomaly-relay/         # webhook LogiCore
├── ui/                    # agent-chat-ui
├── tests/                 # regression-тесты графа и MCP
└── docker-compose.yml
```

Для разработчиков: [DEV.md](DEV.md). Правила внесения изменений:
[AGENTS.md](AGENTS.md).

## Обновление

```bash
git pull
docker compose up -d --build
```

После изменения кода агента для гарантированного обновления всех workers:

```bash
docker compose up -d --force-recreate aegra
```

После изменения только `.env` нужен `docker compose up -d`: обычный `restart`
не перечитывает `env_file`.
