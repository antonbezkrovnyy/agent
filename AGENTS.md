# Правила работы с LogiCore Agent

Действуют для всего репозитория. Запуск и текущие возможности — `README.md`,
архитектура, контракты, ограничения и приёмочные сценарии — `docs/DEV.md`,
только планируемые доработки — `docs/ROADMAP.md`.

## Архитектурные границы

- `agent/graph.py` — supervisor и маршрутизация.
- `agent/quick_graph.py`, `investigation_graph.py`, `reporting_graph.py` — субграфы.
- `agent/subgraph.py` — delta-граница и merge результатов item-веток без отката статусов.
- `agent/scope.py` — structured InvestigationScope до Planner; optional не исполняется.
- `agent/planner.py` — acceptance, supervisor волн work items, не отдельный task manager.
- `agent/agents.py` — runtime `create_agent` и узлы scout/investigator/reporter.
- `agent/skill_runtime.py` — только проверка structured skill_run и anchors, не новый граф.
- `agent/investigation.py` — intent, quick/deep, окна и автоматическая архивация.
- `agent/memory.py` — Case Store, извлечение и консолидация Context Memory.
- `agent/memory_models.py` — Pydantic-схемы дел, фактов и операций памяти.
- `agent/memory_store.py` — предметная SQL-схема в отдельной `agent-memory-db`.
- `agent/artifact_store.py` — S3-ключи, потоковая загрузка и подписанные URL,
  без base64 в постоянном state; data-URL только локальный fallback.
- `agent/artifacts.py` — единый сервис Markdown/ZIP, памяти отчёта и detached hashes;
  `agent/reporting.py` — сборка ZIP во временный файл.
- `agent/analysis.py` — JSONL-датасеты, DuckDB SQL и Evidence, без shell в Aegra.
- `analysis-service/` — изолированный read-only `SELECT` с лимитами, не второй MCP LogiCore.
- `agent/verification_tools.py` — same-source recheck и свежие Evidence;
  `agent/provenance.py` — фактические source/time metadata.
- `agent/verification.py` — статусы доверия, публикация открытых issues и update_failed.
- `agent/metrics.py` — длительность, токены, tool calls и повторы по ролям/items.
- `agent/deadline.py` — оставшееся время и общий технический дедлайн run.
- `agent-mcp/app.py` — асинхронный MCP-адаптер над Aegra runs API, не второй движок.
- `agent-mcp/investigation_mcp_types.py` — TypedDict для MCP structuredContent.
- `agent-evaluator/` — оценка runs, Phoenix annotations и SDK experiments.
- `agent/contracts.py` — Evidence, Scope, WorkItem, Finding, Coverage, CriticIssue, Hypothesis.
- `agent/export.py` — детерминированная полная выгрузка без LLM.
- `agent/critics.py` — прямые структурированные вызовы критиков.
- `agent/evidence.py` — полные Evidence Bundle, обзоры и цепочка расследования.
- `agent/progress.py` — публичный статус run, лимиты и suggestion-действия.
- `agent/observability.py` — OpenInference role/scope/item metadata и prompt version.
- `agent/llm.py` — Pydantic-схемы ответов и LLM.
- `agent/documentation.py` — полная проектная документация в system prompt Chat.
- `litellm/config.yaml` — стабильные `logicore-*` aliases upstream-моделей для Aegra,
  evaluator и embeddings; секреты остаются в `.env` или LiteLLM credential store.
- `agent/state.py` — общие Evidence/Case/Memory Channels и поля supervisor;
  не переносить сюда бизнес-логику и не дублировать общие каналы в двух схемах.
- `agent/tools.py` — MCP tools, Evidence и findings; файлы создаются через artifacts.py.
- `agent/skills/` — Pydantic-контракты, registry и playbooks; исполнение через
  существующий `create_agent`, без отдельного графа skills.
- `agent/execution_audit.py` — фактические model inputs вне checkpoints;
  `control_overrides.py` и `step_mode.py` — opt-in ручное управление без смены автопилота.
- `agent/knowledge.py` — read-only Wiki API и best-effort публикация карточек отчётов.
- `knowledge-service/` — SilverBullet-индекс, Wiki/Memory API и редактор памяти,
  без чата агента и второго investigation engine; `silverbullet/seed/` — стартовые страницы.
- `agui-agent/` — AG-UI-адаптер и валидированный control API над общими Aegra
  threads/runs, без собственного графа и `InMemorySaver`.
- `copilotkit-demo/` — CopilotKit-рабочий стол поверх тех же Aegra checkpoints;
  дерево, prompts, topology, Evidence и ручное управление.

Не возвращать эти области в один большой `agents.py`. Новый субграф оправдан только
самостоятельными input/output, retry или concurrency-семантикой, а не размером файла.

## Инварианты

- В публичном `messages` нет tool-сообщений и ответов с `tool_calls`; приватные
  циклы остаются в `scout_msgs`, `inv_msgs`, `rep_msgs`. Допускаются только сообщения
  пользователя, финальные ответы и детерминированные уведомления `scope`/`planner`/
  `scout`/`finding`/`window`/`hypothesis`.
  Agent Chat UI использует `updates` и единый public reducer: merge по message ID,
  human только от root, разрешённые AI-роли; вложенные `values` не заменяют чат.
- Пользовательские тексты оформляются в читаемый Markdown: короткие абзацы и пункты,
  списки для находок/действий, компактные таблицы для метрик и сравнений. Evidence ID
  не зашиваются в сплошной текст; указываются отдельно от основного повествования.
- Каждый MCP-результат нормализуется через `EvidenceRecord` и сохраняется с
  фактическими аргументами и окном; findings используют `FindingRecord`. IQL приходит
  из MCP и должен совпадать с выполненным запросом, агент не генерирует его повторно.
  Runtime стримит native `create_agent` и события завершённых tools: технический таймаут
  не отбрасывает Evidence, findings, гипотезы, окна, артефакты и приватную историю.
  Отменённые вызовы фиксируются как `cancelled`, вложенные Evidence имеют `parent_call_id`.
- Аналитический отчёт прикрепляется как Markdown плюс самодостаточный ZIP с `report.md`,
  `queries.md`, полными inline `evidence.jsonl`, аудитом каждого вызова `tool_calls.jsonl`,
  `events.csv`, analysis-датасетами/результатами и `manifest.json`. `evidence://` — только
  внутренний ID, не внешняя зависимость. Пакет строится детерминированно и валидирует
  отсутствующие results/ссылки; SHA-256 ZIP и manifest хранятся также вне архива.
  Раздел «Статус проверки и доверие» фиксирует `consistent_with_evidence/unverified/issues_open/update_failed`
  и открытые issues; он всегда есть в файле, а в чат выводится при проблемном результате.
  `consistent_with_evidence` означает только согласованность с пакетом, не независимую
  истинность. Матрица claims различает связь с успешным Evidence, вероятность, спорность
  и неподтверждение; наличие ссылки не является смысловым доказательством. Ссылки `Q1…QN`
  и `query_refs` ведут к воспроизводящим запросам в
  `queries.md`; manifest содержит фактические source/time metadata. `verification_recheck`
  всегда `independent=false` и `same_source_replay`; он не подменяет независимый источник.
  Rework без изменённого Markdown — `update_failed`, а не успех.
- `memory_operations` фиксирует исходное/фактическое действие и исход каждой записи;
  журнал попадает в Case Store, отчёт и manifest. Memory Save пересобирает пакет с сохранением
  verification, claims/`query_refs`, revision и memory-аудита до verification-блока.
  Не заменять журнал одним счётчиком.
- S3 state содержит только object keys и метаданные; подписанные URL создаются при
  выдаче. Ключ включает case, фактический run и SHA-256 содержимого, поэтому новая версия
  не перезаписывает старый объект. Внешний agent-mcp работает только с `inv-*` тредами
  и не дублирует граф. Ответы используют штатный `structuredContent` через TypedDict,
  не JSON в text content.
- Evaluator работает только после успешного run, хранит оценки в `run_evaluations` и
  не меняет Evidence/отчёт. Не публиковать сообщение поверх более нового run.
- Каждый claim содержит существующие Evidence ID; ответ по собранным Evidence требует
  structured claims. `ok` несовместим с открытыми issues. Критики получают только связанные
  с claims/findings/Markdown записи, а большие коллекции — в ограниченной prompt-проекции;
  полные результаты остаются в state и ZIP. Reporter читает записи через batch-инструмент.
  Явные суммы, timeline.total, null/остаток и partial-claims проверяются детерминированно. `has_more=true` допустимо только после
  непустой look-ahead-страницы; пустая — `false`, ошибка проверки — `null` с `pagination_error`.
  Категоричная полнота разрешена только при `has_more=false`; `null` — неизвестность,
  а не доказательство продолжения или завершения. Claims имеют `evidence_class`:
  `полный` запрещён при partial/proxy; `агрегатный` требует агрегатного Evidence, а не одной
  выборки; `прокси` — только предположение. Массовая доля требует числитель и знаменатель
  с одинаковыми окном/фильтром. Первое/последнее событие — `done` только при count=0
  до/после якоря или полном покрытии соответствующей стороны, иначе `partial` и NOT_CHECKED.
  Вторичный источник профилируется, если может изменить основной вывод, без разбора всех top-N.
  Ключевая Deep-аномалия требует альтернативы и дискриминирующий тест, «объясни всплеск» —
  required baseline. Top-N не заменяет профиль распределения; pivot расходуется по ценности.
- InvestigationScope строится structured LLM до Planner. Discovery ограничен двумя
  items; required — максимум двумя. Optional попадает только в отчёт/suggestions.
  Work item содержит acceptance/priority/estimated_cost. Replan разрешён только для
  `acceptance_failed`, `conclusion_invalid` или `missing_critical_evidence`.
- Planner делит deep на work items до Investigator. Supervisor выполняет волны через
  item-subgraphs и `asyncio.gather`, не повторяя завершённые items. Item-worker меняет
  только собственный item; Reporter имеет только `get_evidence_batch`.
- Цепочка расследования строится детерминированно из state. LLM не добавляет в
  неё несуществующие действия и факты.
- Гипотезы меняются только через `update_hypothesis`: `open` допускает отсутствие
  Evidence, `supported/rejected` требуют валидные ID. Чтение выполняется через
  `list_hypotheses`/`get_hypothesis`. История сохраняется в state,
  Case Store и отчёте; open не попадает в долговременную Context Memory.
- Для ответов агентов используются схемы из `llm.py`, а не самодельный parser,
  если доступен structured output. Intent классифицируется LLM; keyword-списки
  для выбора режима/skill запрещены, fallback — консервативный state.
  `needs_new_data/export` имеют приоритет над `inspect_memory`; смена окна в
  continuation требует новых данных. `complex` на пустом деле консервативно нормализуется
  в `needs_new_data=true`, кроме related-case и memory/report маршрутов: Reporter без данных
  недопустим. Обзор памяти — только самостоятельный simple-запрос.
  Явная команда запомнить факт — `remember_fact` → Reporter → `memory_save`, а не
  `inspect_memory`; пользовательский факт сохраняется без Evidence даже на новом треде.
- Windowed tools определяются по JSON Schema (`time_from/time_to`), а не по
  hardcoded списку имён.
- Continuation сохраняет `context.id`, base/focus/history окон, `depth_history`,
  Evidence, полный tool audit, analysis-датасеты/результаты, verification checks, findings
  и гипотезы. Save-only и редактирование отчёта также сохраняют Scope,
  план, work items, `inv_result` и завершённые метрики. Повторный work item получает явный
  handoff только у Investigator и не начинает базовую разведку заново. Идентичный успешный
  MCP-вызов с теми же фактическими аргументами и окном переиспользует Evidence на любой
  попытке внутри того же run; одновременные одинаковые вызовы объединяются через singleflight.
  `run_id`, разные страницы, фильтры и окна образуют разные ключи, поэтому новый run получает
  свежие данные. `rework` и `active_work_item` сбрасываются явно.
- Дела сохраняются только в собственную таблицу `agent_memory.investigations`, не
  в Aegra checkpoints. Восстановление в новом треде требует контрольного запроса.
- Context Memory хранит атомарные факты с provenance и сроком актуальности в
  `agent_memory.context_facts`. Подтверждённый investigation-факт требует валидные
  Evidence; гипотезы не сохраняются. `observed` по умолчанию живёт 24 часа.
  Семантический поиск идёт через pgvector, а найденные факты не считаются свежими
  Evidence и перепроверяются при влиянии на вывод. `agent-memory-db` остаётся единственным
  источником истины для фактов: SilverBullet редактирует их только через Memory API с
  `base_revision`; исправление создаёт новую revision, exclusion исключает из retrieval,
  embedding пересчитывается при смысловом изменении и может быть `pending`.
  SilverBullet-страницы индексируются в `wiki_pages/wiki_sections` и используются только
  через `wiki_search/wiki_read` как методический контекст, не Evidence и не исполняемые skills.
  Карточка отчёта содержит ссылки на S3, а не вторую копию ZIP; сбой knowledge-service
  не роняет расследование и сохранение отчёта.
- `search_similar_cases` возвращает только компактные карточки похожих дел, не
  continuation и не текущие Evidence. Похожее дело не заменяет `context.id`.
- Ошибка памяти не роняет диалог. OIDC/multiuser не реализуем.
- Модели по ролям задаются env, суммаризация — только `SummarizationMiddleware`.
- Skills берутся только из registry. `skill_run` сохраняет id/version и Evidence;
  отсутствующий или несовместимый результат помечается failed, а не успешным.
- Anchors требуют Evidence и канонизируются как `type:value`. Повторные ветки,
  превышение depth/branches и непроверенные anchors явно попадают в `NOT_CHECKED`.
- Явные `time_from/time_to` tool call не перезаписываются контекстом. Смена окна
  выполняется через `update_window` и не создаёт новое расследование.
- Обязательных подтверждений действий, approval-интерраптов и slash-команд нет.
  Ручные pause/resume и tool-step interrupts допустимы только по явному opt-in.
  State edits проходят валидацию; откат создаёт ветку checkpoint, ручные MCP-вызовы
  read-only и создают Evidence. Явная новая тема архивируется перед очисткой;
  `uncertain` продолжает текущий случай.
- Полная выгрузка использует универсальный `extract_data`; не добавлять
  ping-specific или другие одноразовые дубликаты этого инструмента. Составной GROUP BY
  выполняется через `aggregate_events`, ad-hoc вычисления — через JSONL и изолированный
  DuckDB `analysis_sql`. Точный COUNT DISTINCT запрещён при `complete=false`; после
  неуспешных агрегатов используется SQL fallback. Произвольный shell/Python в Aegra запрещён.
  Запрошенные колонки валидируются через `field_lookup`, `unavailable_columns` явно делают
  датасет partial; значения дополняются `NULL`. Идентичные полные dataset/SQL переиспользуются,
  цикл одинаковых выгрузок недопустим; SQL-ошибки имеют machine-readable recovery, а не
  запускают новую выгрузку. На run допускается до 6 `analysis_sql` с исправлением запроса,
  седьмой вызов — `sql_attempt_limit`. Критик отклоняет текст, противоречащий артефактам.
- Пользовательского бюджета времени нет. Технический `run_deadline_at` задаётся один раз
  на run через `AGENT_RUN_TIMEOUT_SECONDS`, ограничивает все роли и новые волны; прямой
  LLM-запрос ограничен `LLM_REQUEST_TIMEOUT_SECONDS`. Не возвращать пользовательский
  `budget_seconds` или выбор времени через interrupt без отдельного решения.
- Критики не получают MCP-инструменты. Их сбой обозначается `critic_unavailable`,
  а не превращается в `ok` и не останавливает run.
- Короткие уведомления хранятся в отдельном `progress`, а не в `messages` и не
  передаются LLM. Suggestion-кнопки отправляют обычное follow-up сообщение.

## Лимиты

Текущие `run_limit`: scout 10, investigator 30, reporter 60. Значения и остальные
лимиты задаются в `agent/config.py` через env и сбрасываются на каждый новый запуск
роли. Технические таймауты: soft 600 с, hard 900 с на роль, export 120 с,
LLM-запрос 300 с, общий run 1800 с. После soft Scout/Investigator/Reporter теряют
investigative tools и получают до 10 попыток
structured finalization по 300 с внутри общего hard deadline; fallback сохраняет partial state.

При изменении лимитов обновлять тест `tests/test_tool_limit.py`, `README.md` и
`docs/DEV.md`. Не описывать tool limit как общий лимит чата: это неверно. У Scout 10
выполненных вызовов на запуск без continuation. Investigator и Reporter после лимита
автоматически продолжают работу со сброшенным `run_tool_call_count`; fallback без
инструментов — после `AGENT_MAX_CONTINUATIONS` или при `no_progress`. Investigator
не делает новый continuation/rework без новых Evidence: item становится partial/failed
с открытым acceptance. Сырой текст middleware не является пользовательским ответом.
Несколько Evidence читать через batch-инструмент.

## Изменения кода

- Сохранять существующие имена модулей и точки входа `graph.py:graph`.
- Не добавлять зависимости без подтверждения их наличия в проекте.
- Сначала использовать официальные LangChain/LangGraph primitives. Собственный
  framework писать только для предметной логики, которой у библиотек нет.
- Не менять UI для обхода ошибки агента, если причина находится в графе.
- После изменения поведения обновлять устаревшие комментарии, docstrings,
  `README.md` и `docs/DEV.md`.
- Не читать и не коммитить `.env`; пример конфигурации — `.env.example`.

## Проверки

Из корня репозитория:

```bash
uv run --with "langchain==1.3.10" --with "langgraph==1.2.16" \
  --with "langchain-openai" --with "pydantic-settings" \
  --with "httpx" --with "tenacity" --with "pytest" --with "loguru" \
  --with "psycopg" --with "mcp[cli]>=2,<3" \
  --with "fastapi" --with "uvicorn" --with "duckdb>=1,<2" pytest -q tests

git diff --check
```

Для применения кода агента ко всем workers:

```bash
docker compose up -d --force-recreate aegra
curl http://localhost:2026/health
```

Перед завершением проверить исходный пользовательский сценарий через реальный
API на новом треде. Не считать успешный импорт или unit-тесты заменой live
проверки. Не оставлять фоновые процессы и созданные проверками файлы без
необходимости.
