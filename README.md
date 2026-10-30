# logicore_agent

Агент расследования аномалий Логикора. MCP-сервер тянет данные со стенда (Mars API),
агент расследует, всё крутится на agra. Одна docker-стопка.

## Сервисы

| Сервис | Порт | Описание |
|--------|------|----------|
| `aegra` | 2026 | рантайм 0.11.x, агент из `agent/`, сборка из `github.com/aegra/aegra` |
| `agent-chat-ui` | 3000 | веб-UI, форк agra-UI (Next.js) |
| `logicore-mcp` | 9000, только docker-сеть | MCP-сервер Логикора, 11 read-only тулов |
| `anomaly-relay` | 2025 | приёмник аномали-вебхука Логикора: тред + run в agra, доставка отчёта |
| `postgres` (pgvector) | 5432 | хранилище agra |
| `redis` | 6379 | очередь задач agra |
| `phoenix` | 6006 | трейсинг |

## Установка

Docker с compose v2. На Astra Linux:

```
sudo apt install docker.io docker-compose-v2
```

```
git clone https://gitlab.jet.su/aa.bezkrovnyy/logicore_agent.git
cd logicore_agent
cp .env.example .env
docker compose up -d --build
```

Секреты заполняются в .env, в репо лежит только .env.example:

- LLM_BASE_URL, LLM_TOKEN, LLM_MODEL — LLM-провайдер (OpenAI-совместимый)
- LOGIQ_URL, LOGIQ_USER, LOGIQ_PASSWORD, LOGIQ_TLS_VERIFY — стенд Логикора
- LOGIQ_API_TOKEN — токен MCP-сервера
- POSTGRES_PASSWORD — пароль postgres
- RELAY_TOKEN — опционально, если задан, вебхук требует Bearer
- DEFAULT_CALLBACK_URL — опционально, куда слать отчёт по умолчанию

Первая сборка долгая. Когда закончится, `docker compose ps`: agra, logicore-mcp,
anomaly-relay должны быть healthy.

UI http://localhost:3000, API http://localhost:2026.
Аномали-вебхук Логикора настраивать на http://\<хост\>:2025/anomaly.

## Обновление

```
git pull
docker compose up -d --build
```

Менялся только .env — `docker compose up -d`: env_file читается при создании контейнера,
restart новые переменные не подхватит.

## Структура

```
├── docker-compose.yml   # стопка сервисов
├── aegra.json           # декларация графов agra
├── agent/               # агент: graph.py, mcp_client.py, config.py
├── logicore-mcp/        # MCP-сервер (Mars API)
├── anomaly-relay/       # аномали-вебхук → agra
└── ui/                  # agent-chat-ui
```
