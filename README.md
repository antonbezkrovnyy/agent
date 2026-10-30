# logicore_agent

Стек агента расследования аномалий Логикора: MCP-сервер (Mars API) → LangGraph-агент → agra-рантайм,
всё в одной docker-стопке.

## Сервисы

| Сервис | Порт | Назначение |
|--------|------|------------|
| `aegra` | 2026 | agra-рантайм (0.11.x) с custom-агентом из `agent/`; сборка из `github.com/aegra/aegra` |
| `agent-chat-ui` | 3000 | веб-UI агента (fork agra-UI, Next.js) |
| `logicore-mcp` | 9000 (только docker-сеть) | MCP-сервер Логикора: Mars API, 11 read-only тулов, streamable-http + bearer-токен |
| `anomaly-relay` | 2025 | приёмник нативного аномали-вебхука Логикора → тред в agra → run расследования → отчёт по `callback_url` |
| `postgres` (pgvector) | 5432 | хранилище agra |
| `redis` | 6379 | брокер задач agra |
| `phoenix` | 6006 | трейсинг (OTLP) |

## Запуск

```bash
cp .env.example .env                 # переменные docker compose (POSTGRES_PASSWORD, RELAY_TOKEN, …)
cp agent/.env.example agent/.env     # LLM_TOKEN / LOGIQ_* / LOGIQ_API_TOKEN — заполнить реальными
docker compose up --build
```

UI: http://localhost:3000 · API agra: http://localhost:2026 · relay: http://localhost:2025

## Структура

```
├── docker-compose.yml   # стопка сервисов
├── aegra.json           # декларация графов agra (custom agent)
├── agent/               # кастомный LangGraph-агент: graph.py, mcp_client.py, config.py, register.py
├── logicore-mcp/        # MCP-сервер Логикора (Mars API): слои domain/ + earth/ + mcp/, 11 тулов
├── anomaly-relay/       # отдельный docker-сервис: нативный аномали-вебхук → agra
└── ui/                  # agent-chat-ui (fork agra-UI, Next.js)
```

## Секреты

В репозиторий попадают только `.env.example`. Реальные значения — в локальных `.env`
(корневой и `agent/`), оба в gitignore: LLM-токен, креды Логикора-стенда, bearer-токен MCP.
