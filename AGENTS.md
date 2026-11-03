# Правила работы с LogiCore Agent

Действуют для всего репозитория. Запуск и текущие возможности — `README.md`,
архитектура, контракты, ограничения и приёмочные сценарии — `docs/DEV.md`.

## Архитектурные границы

- `agent/graph.py` — supervisor и маршрутизация.
- `agent/quick_graph.py`, `investigation_graph.py`, `reporting_graph.py` — субграфы.
- `agent/subgraph.py` — delta-граница; additive state не должен дублироваться.
- `agent/agents.py` — runtime `create_agent` и узлы scout/investigator/reporter.
- `agent/investigation.py` — intent, quick/deep, окна и автоматическая архивация.
- `agent/memory.py` — Case Store, извлечение и консолидация Context Memory.
- `agent/memory_models.py` — Pydantic-схемы дел, фактов и операций памяти.
- `agent/memory_store.py` — предметная SQL-схема в отдельной `agent-memory-db`.
- `agent/contracts.py` — Evidence, Finding, Coverage, CriticIssue и Hypothesis.
- `agent/export.py` — детерминированная полная выгрузка без LLM.
- `agent/critics.py` — прямые структурированные вызовы критиков.
- `agent/evidence.py` — полные Evidence Bundle, обзоры и цепочка расследования.
- `agent/progress.py` — публичный статус run, лимиты и suggestion-действия.
- `agent/llm.py` — Pydantic-схемы ответов и LLM.
- `agent/state.py` — состояние; не переносить сюда бизнес-логику.
- `agent/tools.py` — MCP tools, Evidence, findings и отчёты.
- `agent/skills/` — Pydantic-контракты, registry и playbooks; исполнение через
  существующий `create_agent`, без отдельного skill-runtime.

Не возвращать эти области в один большой `agents.py`.

## Инварианты

- В публичном `messages` нет tool-сообщений и ответов с `tool_calls`; приватные
  циклы остаются в `scout_msgs`, `inv_msgs`, `rep_msgs`.
- Каждый MCP-результат нормализуется через `EvidenceRecord` и сохраняется с
  фактическими аргументами и окном; findings используют `FindingRecord`.
- Claims ссылаются только на существующие Evidence ID. Критик получает полные
  записи по этим ID без скрытого усечения; агенты читают их через `get_evidence`.
- Цепочка расследования строится детерминированно из state. LLM не добавляет в
  неё несуществующие действия и факты.
- Гипотезы меняются только через `update_hypothesis`: `open` допускает отсутствие
  Evidence, `supported/rejected` требуют валидные ID. История сохраняется в state,
  Case Store и отчёте; open не попадает в долговременную Context Memory.
- Для ответов агентов используются схемы из `llm.py`, а не самодельный parser,
  если доступен structured output. Intent классифицируется LLM; keyword-списки
  для выбора режима/skill запрещены, fallback — консервативный state.
- Windowed tools определяются по JSON Schema (`time_from/time_to`), а не по
  hardcoded списку имён.
- Continuation сохраняет `context.id`, base/focus/history окон, `depth_history`,
  Evidence, findings и гипотезы.
- Дела сохраняются только в собственную таблицу `agent_memory.investigations`, не
  в Aegra checkpoints. Восстановление в новом треде требует контрольного запроса.
- Context Memory хранит атомарные факты с provenance и сроком актуальности в
  `agent_memory.context_facts`. Подтверждённый investigation-факт требует валидные
  Evidence; гипотезы не сохраняются. `observed` по умолчанию живёт 24 часа.
  Семантический поиск идёт через pgvector, а найденные факты не считаются свежими
  Evidence и перепроверяются при влиянии на вывод.
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
- Подтверждений действий, approval-интерраптов и slash-команд нет. Явная новая
  тема автоматически архивируется перед очисткой; `uncertain` продолжает текущий случай.
- Полная выгрузка использует универсальный `extract_data`; не добавлять
  ping-specific или другие одноразовые дубликаты этого инструмента.
- Пользовательского бюджета времени нет. Не возвращать `deadline_at`,
  `budget_seconds` или выбор времени через interrupt без отдельного решения.
- Критики не получают MCP-инструменты. Их сбой обозначается `critic_unavailable`,
  а не превращается в `ok` и не останавливает run.
- Короткие уведомления хранятся в отдельном `progress`, а не в `messages` и не
  передаются LLM. Suggestion-кнопки отправляют обычное follow-up сообщение.

## Лимиты

Текущие `run_limit`: scout 10, investigator 30, reporter 60. Значения и остальные
лимиты задаются в `agent/config.py` через env и сбрасываются на каждый новый
`ainvoke`. Технические таймауты: агент 600 с, export 120 с.

При изменении лимитов обновлять тест `tests/test_tool_limit.py`, `README.md` и
`docs/DEV.md`. Не описывать tool limit как общий лимит чата: это неверно.

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
  --with "psycopg" pytest -q tests

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
