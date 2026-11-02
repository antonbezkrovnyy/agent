# Правила работы с LogiCore Agent

Действуют для всего репозитория. Пользовательская документация — `README.md`,
подробная текущая архитектура — `DEV.md`, планируемые доработки —
`INVESTIGATION_PLAN.md`.

## Архитектурные границы

- `agent/graph.py` — только внешний граф и маршрутизация.
- `agent/agents.py` — runtime `create_agent` и узлы scout/investigator/reporter.
- `agent/investigation.py` — intent, окно, архив и подтверждение расследования.
- `agent/export.py` — детерминированная полная выгрузка без LLM.
- `agent/critics.py` — прямые структурированные вызовы критиков.
- `agent/progress.py` — публичный статус run, лимиты и suggestion-действия.
- `agent/llm.py` — Pydantic-схемы ответов и LLM.
- `agent/state.py` — состояние; не переносить сюда бизнес-логику.
- `agent/tools.py` — MCP tools, Evidence, findings и отчёты.

Не возвращать эти области в один большой `agents.py`.

## Инварианты

- В публичном `messages` нет tool-сообщений и ответов с `tool_calls`; приватные
  циклы остаются в `scout_msgs`, `inv_msgs`, `rep_msgs`.
- Каждый MCP-результат нормализуется в JSON и сохраняется как Evidence с
  фактическими аргументами и окном.
- Claims ссылаются только на существующие Evidence ID.
- Для ответов агентов используются схемы из `llm.py`, а не самодельный parser,
  если доступен structured output.
- Continuation сохраняет `context.id`, окно, Evidence и findings.
- Новое расследование очищает данные только после архивации и подтверждения.
- Полная выгрузка использует универсальный `extract_data`; не добавлять
  ping-specific или другие одноразовые дубликаты этого инструмента.
- Пользовательского бюджета времени нет. Не возвращать `deadline_at`,
  `budget_seconds` или выбор времени через interrupt без отдельного решения.
- Критики не получают MCP-инструменты.
- Статус хранится в отдельном `progress`, а не в `messages` и не передаётся LLM.
- Resume-кнопки отвечают на interrupt; suggestion-кнопки отправляют обычное
  follow-up сообщение. Не смешивать эти два сценария.

## Лимиты

Текущие `run_limit`: scout 10, investigator 30, reporter 60. Значения заданы в
`agent/progress.py` и сбрасываются на каждый новый `ainvoke`. Технические
таймауты: агент 600 с, export 120 с; значения — `agent/config.py`.

При изменении лимитов обновлять тест `tests/test_tool_limit.py`, `README.md` и
`DEV.md`. Не описывать tool limit как общий лимит чата: это неверно.

## Изменения кода

- Сохранять существующие имена модулей и точки входа `graph.py:graph`.
- Не добавлять зависимости без подтверждения их наличия в проекте.
- Сначала использовать официальные LangChain/LangGraph primitives. Собственный
  framework писать только для предметной логики, которой у библиотек нет.
- Не менять UI для обхода ошибки агента, если причина находится в графе.
- После изменения поведения обновлять устаревшие комментарии, docstrings,
  `README.md` и `DEV.md`.
- Не читать и не коммитить `.env`; пример конфигурации — `.env.example`.

## Проверки

Из корня репозитория:

```bash
uv run --with "langchain==1.3.10" --with "langgraph==1.2.16" \
  --with "langchain-openai" --with "pydantic-settings" \
  --with "httpx" --with "tenacity" --with "pytest" --with "loguru" \
  pytest -q tests

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
