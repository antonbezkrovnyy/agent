# LogiCore Agent

Многоагентная система для анализа событий LogiCore. LangGraph управляет диалогом,
LangChain выполняет приватные tool-циклы агентов, MCP-сервер читает данные через
Mars API, Aegra предоставляет API и checkpoints, Phoenix собирает трассировку.

Документы разделены по назначению:

- **этот README** — что работает сейчас и как запустить;
- [DEV.md](docs/DEV.md) — полное руководство по работе и устройству агента;
- [NEXT_STEPS.md](docs/NEXT_STEPS.md) — рекомендации по качеству и дальнейшему развитию;
- [AGENTS.md](AGENTS.md) — правила внесения изменений.

## Что работает сейчас

- Обычный чат без обращения к LogiCore.
- Структурированный intent: simple, complex, continuation, export и выбор
  `work_mode=quick/deep`.
- Quick отвечает через scout → reporter без глубокого расследования; при нехватке
  данных или найденной зацепке автоматически переходит в deep.
- Deep идёт через investigator и может сам менять окно через `update_window`.
- Skills `incident_dating`, `event_drilldown` и `entity_pivot` v1 автоматически
  выбираются по задаче. Есть адаптивные окна, разбор `uuid + timestamp`, typed
  anchors, дедупликация `type:value`, ограничения глубины/ширины и `NOT_CHECKED`.
- Поиск, подсчёт и агрегаты через 12 read-only MCP-инструментов.
- Отдельные Quick, Deep Investigation и Reporting-субграфы с delta-границей,
  которая не дублирует additive state в supervisor.
- Приватные tool-циклы scout, investigator и reporter через `create_agent` и
  `SummarizationMiddleware` для длинной истории.
- Собственная `agent-memory-db`: Case Store в предметной таблице дел и Context Memory
  атомарных фактов с provenance, pgvector-поиском и консолидацией после расследования.
  Восстановление дела в новом треде требует контрольного запроса к LogiCore.
- Отдельный `search_similar_cases` находит компактные карточки похожих дел без
  continuation и подмены текущих Evidence.
- Журнал гипотез `open/supported/rejected` с историей, Evidence ID и отображением
  в детерминированной цепочке отчёта.
- Pydantic-контракты Evidence/Finding, расширенные Scout/Intent/Reporting и
  структурированные `issues` критиков с `suggested_action`.
- Модели по ролям через env; `model` и фактическая `duration_ms` в `skill_runs`.
- `Evidence` с фактическими аргументами, окном, статусом и результатом вызова.
  Явные `time_from/time_to` запроса не перезаписываются контекстом.
- Компактный обзор Evidence для агентов, полный `Evidence Bundle` по claims для
  критика и локальный `get_evidence(id)` без повторного MCP-запроса.
- Детерминированная цепочка «задача → вызовы → окна → режимы → находки →
  ограничения → NOT_CHECKED» автоматически добавляется в файлы отчётов.
- Проверяемый ZIP-пакет расследования: `report.md`, фактические IQL/JSON-запросы,
  `evidence.jsonl`, `events.csv` и `manifest.json` с индексом Evidence, журналом
  памяти и SHA-256. В отчёте видно, какие факты LLM предложила, сохранила или отклонила.
- SeaweedFS/S3-хранение файлов по ключам `investigations/<case>/<run>/<file>`;
  в state остаются метаданные, в чат и callback выдаются подписанные URL с TTL.
- Асинхронный MCP-интерфейс самого агента: старт, статус, результат, отмена и
  получение Evidence без ожидания долгого run и без base64 в structured JSON.
- Структурированные ответы агентов и критиков с текстовым fallback. Сбой критика
  обозначается как `critic_unavailable`, а не маскируется под `ok`.
- Continuation с сохранением `context.id`, `base_window`, `focus_window`,
  `window_history`, `depth_history`, Evidence и findings.
- Полный автопилот без подтверждений, resume и slash-команд. Явная новая тема
  автоматически архивирует прежний отчёт; неоднозначная — продолжает текущий случай.
- Детерминированный `extract_data`: серверная пагинация, таблица и Markdown-файл
  без генерации сотен строк через LLM.
- Короткие уведомления в чате через одну карточку `progress`: quick/deep, смена
  этапа или окна и счётчик вызовов. Служебные сообщения не попадают в `messages`.
- Suggestion-кнопки после ответа.
- Трассировка в Phoenix и regression-тесты графа/MCP.

Multiuser/OIDC намеренно не реализуется. Текущий anonymous-режим предназначен
только для локальной разработки.

## Сервисы

| Сервис | Порт | Назначение |
|---|---:|---|
| `aegra` | 2026 | LangGraph/Aegra API, workers и checkpoints |
| `agent-chat-ui` | 3000 | Веб-чат на Next.js |
| `logicore-mcp` | 9000, только Docker-сеть | 12 read-only MCP-инструментов |
| `agent-mcp` | 9100 | Асинхронный MCP-интерфейс расследований |
| `seaweedfs` | 8333 | S3-файлы отчётов и пакетов Evidence |
| `anomaly-relay` | 2025 | Webhook LogiCore → тред и run |
| `postgres` | 5432 | Состояние Aegra, не отдельная память случаев |
| `agent-memory-db` | только Docker-сеть | Case Store и Context Memory в схеме `agent_memory`, pgvector |
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

- `LLM_BASE_URL`, `LLM_TOKEN`, `LLM_MODEL` — OpenAI-совместимый провайдер;
- `LLM_MODEL_INTENT/SCOUT/INVESTIGATOR/REPORTER/CRITIC/MEMORY` — необязательные модели по ролям;
- `SUMMARIZATION_TRIGGER_TOKENS`, `SUMMARIZATION_KEEP_MESSAGES` — суммаризация истории;
- `AGENT_TOOL_LIMIT_SCOUT/INVESTIGATOR/REPORTER`, `AGENT_TIMEOUT_SECONDS`,
  `EXPORT_TIMEOUT_SECONDS`, `MAX_REWORK` — лимиты и таймауты;
- `CRITIC_MESSAGE_LIMIT`, `CRITIC_DIALOG_LIMIT`, `REPORT_INJECT_LIMIT` — размеры контекста;
- `LOGIQ_URL`, `LOGIQ_USER`, `LOGIQ_PASSWORD`, `LOGIQ_TLS_VERIFY` — LogiCore;
- `LOGIQ_API_TOKEN` — авторизация MCP streamable-http;
- `POSTGRES_PASSWORD` — пароль Postgres Aegra;
- `AGENT_MEMORY_POSTGRES_PASSWORD`, `AGENT_MEMORY_RECENT_LIMIT` — отдельная память;
- `AGENT_MEMORY_SCOPE`, `AGENT_MEMORY_RETRIEVAL_LIMIT`, `AGENT_MEMORY_CASE_RETRIEVAL_LIMIT`,
  `AGENT_MEMORY_SIMILARITY_THRESHOLD` — область и параметры поиска Context Memory и похожих дел;
  `AGENT_MEMORY_OBSERVED_TTL_HOURS=24` — TTL наблюдений;
- `AGENT_MEMORY_EMBEDDING_MODEL=bge-m3`, `AGENT_MEMORY_EMBEDDING_DIMENSIONS=1024` —
  embeddings; отдельные `AGENT_MEMORY_EMBEDDING_BASE_URL/TOKEN` необязательны;
- `ARTIFACT_S3_*`, `ARTIFACT_SIGNED_URL_TTL_SECONDS` — S3 и срок действия ссылок;
- `AGENT_MCP_TOKEN` — Bearer-авторизация внешнего MCP агента;
- `RELAY_TOKEN`, `DEFAULT_CALLBACK_URL` — опциональные webhook/отчёты;
- `DEPLOY_HOST/USER/PASSWORD/GIT_URL/REMOTE_PATH` — опциональный `scripts/deploy_ma.py`.
  Все значения по умолчанию и дополнительные лимиты перечислены в `.env.example`.

После запуска:

- чат: http://localhost:3000;
- API: http://localhost:2026;
- Phoenix: http://localhost:6006;
- webhook: `http://<хост>:2025/anomaly`.

Проверка:

```bash
docker compose ps
curl http://localhost:2026/health
```

Anonymous-режим подходит только для локальной разработки.

## MCP агента

Endpoint: `http://localhost:9100/mcp`, заголовок `Authorization: Bearer $AGENT_MCP_TOKEN`.
Вызовы возвращаются сразу; долгий run продолжается в Aegra:

- `investigation_start(task, window?, mode?)` → `investigation_id`;
- `investigation_status(investigation_id)` → этап, прогресс и ошибки;
- `investigation_result(investigation_id)` → отчёт, аудит памяти и свежие S3-ссылки;
- `investigation_evidence(investigation_id, evidence_id?)` → полные Evidence;
- `investigation_cancel(investigation_id)` → отмена активного run.

Адаптер использует существующий граф и runs API. Доступ разрешён только к созданным
через него тредам `inv-*`; второго investigation engine или task manager нет.

## Текущий граф

```mermaid
flowchart TD
    START([START]) --> entry --> memory_context --> intent
    intent -->|simple| chat --> END([END])
    intent -->|related case| memory_load --> memory_retrieve
    intent -->|export_data| export --> memory_save
    intent -->|new + история| archive
    intent -->|quick или deep| memory_retrieve
    intent -->|ответ или правка отчёта| reporting
    archive -->|quick или deep| memory_retrieve
    archive -->|export| export
    memory_retrieve -->|quick| quick
    memory_retrieve -->|deep| deep

    subgraph quick[Quick Investigation]
      scout
    end
    scout -->|достаточно данных| reporting
    scout -->|нужно углубление| deep

    subgraph deep[Deep Investigation]
      investigator --> critic_evidence
      critic_evidence -->|rework_data| investigator
    end
    deep --> reporting

    subgraph reporting[Reporting]
      reporter --> critic_report
      critic_report -->|rework_report| reporter
    end
    critic_report -->|rework_data| deep
    reporting -->|готово| memory_save --> END
    memory_retrieve -. чтение фактов и похожих дел .-> DB[(agent_memory)]
    memory_save --> DB
```

При `CRITIC_ENABLED=false` критики пропускаются. `export` не вызывает LLM и не
проходит критиков.

Публичный `messages` содержит только сообщения пользователя и финальные ответы.
Tool-циклы остаются в `scout_msgs`, `inv_msgs`, `rep_msgs`.

`memory_retrieve` ищет до пяти релевантных фактов и до трёх похожих дел при старте
quick/deep. Scout и investigator могут повторить поиск через `search_context_memory`
и `search_similar_cases` при появлении новых сущностей. Похожие дела возвращаются
компактными карточками и не становятся continuation. После ответа `memory_save`
записывает дело в `agent_memory.investigations`,
а structured LLM-шаг предлагает операции `insert/update/supersede/ignore` для атомарных
фактов в `agent_memory.context_facts`. Подтверждённые investigation-факты без валидных
Evidence отклоняются; найденный контекст не подменяет свежие запросы к LogiCore.

Подробности состояния и маршрутов — в [DEV.md](docs/DEV.md).

## Субграфы и память (реализовано)

Логическая схема подсистем и внешних хранилищ:

```mermaid
flowchart TD
    U[Пользователь] --> A[Supervisor]

    A -->|simple| C[Chat]
    A -->|export| E[Deterministic Export]
    A -->|quick| Q[Quick Investigation subgraph]
    A -->|deep| I[Deep Investigation subgraph]

    Q -->|достаточно данных| R[Reporting subgraph]
    Q -->|нужны причина, pivot или другое окно| I
    I --> H[Окна, Evidence, findings, гипотезы и skills]
    H --> CE[Critic Evidence]
    CE --> IR[InvestigationResult]
    IR --> A

    A --> R
    R --> CR[Critic Report]
    CR --> DONE[Ответ, цепочка, файл и suggestions]

    A <--> M[memory_context / memory_retrieve / memory_save]
    M <--> DB[(agent_memory.investigations + context_facts)]
    Q --> MCP[logicore-mcp]
    H --> MCP
    E --> MCP
```

Quick и deep используют общие Evidence и handoff, но имеют разные бюджеты,
модели и жизненный цикл. Полные контракты находятся в [DEV.md](docs/DEV.md).

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
новое сообщение, rework или retry structured response. Это не общий лимит чата.
Autopilot не отменяет эти ограничения.

При исчерпании tool limit middleware завершает текущий tool-цикл, внешний граф
продолжает работу. Таймаут одного агента не ограничивает длительность всего run:
прямые вызовы intent, chat и критиков отдельным таймаутом сейчас не покрыты.

## Текущие ограничения и велосипеды

Не весь собственный код является велосипедом: Evidence, окна, anchors, pivot,
`extract_data`, `progress` и отчёты относятся к предметной логике. Но есть явные
места для исправления.

| Место | Проблема сейчас | Как исправлять |
|---|---|---|
| Structured response fallback | Ручной function-call parser удалён; остаётся только явный текстовый fallback в схему | Проверять необходимость fallback на живом провайдере, не восстанавливать собственный parser |
| Метрики skills | `model`, `duration_ms`, Evidence и окна сохраняются в `skill_runs` и памяти | Использовать для наблюдаемости; сравнение версий skills не требуется |

## Реализованные этапы

1. **Quick/deep и автономные окна** — отдельные субграфы.
2. **Полные Evidence и цепочка расследования** — state, отчёты и память.
3. **Понятный автопилот** — короткие уведомления без подтверждений и slash-команд.
4. **Skills** — `incident_dating`, `event_drilldown`, `entity_pivot` с метриками.
5. **Расширенные контракты и гипотезы** — Evidence/Finding, Scout, Intent, Critic issues,
   `open/supported/rejected` с историей.
6. **Case Store + Context Memory + похожие дела** — собственные таблицы, pgvector,
   консолидация, continuation и `search_similar_cases`.
7. **Модели и метрики** — модели по ролям, `model`/`duration_ms`; сравнение версий
   skills по решению не выполняется.
8. **Проверяемые отчёты** — фактический IQL, ZIP с Evidence и аудит памяти.
9. **S3-артефакты** — SeaweedFS, object keys и обновляемые signed URL.
10. **MCP агента** — асинхронные runs, отчёты, Evidence и отмена для других агентов.

Этапы 1–4 образуют MVP. Архитектура, контракты и приёмочные сценарии —
[DEV.md](docs/DEV.md).

## Структура проекта

```text
├── agent/
│   ├── graph.py           # supervisor и маршруты
│   ├── quick_graph.py     # Quick Investigation subgraph
│   ├── investigation_graph.py # Deep Investigation subgraph
│   ├── reporting_graph.py # Reporting subgraph
│   ├── subgraph.py        # delta-граница субграфов
│   ├── agents.py          # runtime scout/investigator/reporter
│   ├── investigation.py   # intent, quick/deep, окна и автоархивация
│   ├── memory.py          # Case Store и консолидация Context Memory
│   ├── memory_models.py   # схемы дел, фактов и операций
│   ├── memory_store.py    # предметные таблицы и pgvector-поиск
│   ├── artifact_store.py  # S3-ключи и подписанные ссылки
│   ├── contracts.py       # Evidence, Finding, Coverage, CriticIssue, Hypothesis
│   ├── export.py          # детерминированная таблица и файл
│   ├── critics.py         # критики доказательств и отчёта
│   ├── evidence.py        # Evidence Bundle и цепочка расследования
│   ├── progress.py        # статус, лимиты и suggestion-действия
│   ├── llm.py             # LLM и Pydantic-схемы ответов
│   ├── state.py           # внешнее и внутреннее состояние
│   ├── tools.py           # MCP tools, Evidence, отчёты
│   ├── skills/            # registry, контракты и playbooks
│   └── prompts/           # системные промпты
├── logicore-mcp/          # MCP-сервер и пагинация Mars API
├── agent-mcp/             # Асинхронный MCP-интерфейс расследований
├── anomaly-relay/         # webhook LogiCore
├── ui/                    # agent-chat-ui
├── docs/DEV.md            # архитектура, контракты и приёмка
├── scripts/deploy_ma.py   # опциональный удалённый deploy через env
├── tests/                 # regression-тесты графа и MCP
├── aegra.json             # регистрация графа
└── docker-compose.yml
```

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
