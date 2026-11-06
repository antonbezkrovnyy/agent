# Наблюдаемость через Arize Phoenix

Документ описывает реализованную интеграцию. Невыполненные production-проверки —
[ROADMAP.md](ROADMAP.md).

## Состав

- Aegra экспортирует OpenTelemetry/OpenInference traces в Phoenix.
- `agent-evaluator` читает traces через Phoenix GraphQL, сохраняет оценки в
  `agent_memory.run_evaluations` и публикует «Автодиагностику» в исходный тред.
- Оценка также записывается как Phoenix trace annotation `logicore_agent_quality`
  через официальный `phoenix.client.Client`.
- `agent-evaluator/experiments.py` создаёт datasets и запускает experiments через
  официальный Phoenix SDK; собственного experiment framework нет.

## Project и endpoints

| Настройка | Значение по умолчанию |
|---|---|
| `PHOENIX_PROJECT` | `logicore-agent` |
| `OTEL_SERVICE_NAME` | `logicore-agent` |
| `OTEL_RESOURCE_ATTRIBUTES` | `openinference.project.name=logicore-agent` |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://phoenix:6006/v1/traces` |
| `PHOENIX_ENDPOINT` / `PHOENIX_URL` | `http://phoenix:6006` |
| `PHOENIX_PUBLIC_URL` | `http://localhost:6006` |
| Phoenix UI | http://localhost:6006 |
| Phoenix MCP | http://localhost:6006/mcp |

## Metadata, tags и версии

Aegra передаёт `run_id`, `thread_id`, `session_id`, `user_id`, LangGraph node/step,
checkpoint namespace, модель, provider и токены. `agent/observability.py` добавляет:

- `agent_role` — scope, intent, planner, scout, investigator, reporter, critics, chat, memory;
- `work_mode` — quick/deep;
- `scope_mode` — discovery/targeted/alert;
- `investigation_id`, `work_item_id`, `work_item_attempt`, `skill_id`;
- `agent_phase` — `investigate`, `finalize` или `fallback`;
- `continuation` и `termination_reason` — `done`, `soft_timeout`, `provider_error`,
  `finalize_exhausted`, `hard_timeout` или `running`;
- `prompt_version` из `AGENT_PROMPT_VERSION`.

Tags: `role:<role>`, `mode:<mode>`, `scope:<scope>`. Metadata не содержит тексты
пользователей, секреты или содержимое Evidence. Входной `ToolRuntime` выводится как
`ToolRuntime(<injected>)`: полный state, сообщения, Evidence и память не дублируются
в observability-входе, а фактические аргументы инструмента сохраняются. Это позволяет
фильтровать spans и сравнивать versions без передачи чувствительных данных.

## Метрики run и items

`agent/metrics.py` записывает для каждой роли и work item:

- start/finish и длительность;
- модель, prompt/completion/total tokens;
- вызовы инструментов по именам и повторы одинаковых запросов;
- число автоматических continuations;
- `agent_phase`, `continuation` и `termination_reason`, чтобы отличать обычную работу,
  finalization, fallback и повторный заход; завершённые до таймаута вызовы не обнуляются.

Метрики попадают в `run_metrics`, work item, Case Record, `manifest.json` и ответ
`investigation_result`. Evaluator добавляет долю unsupported claims, replan count,
LLM/tool latency по OpenInference span kind и `scope_creep`, если текущий план превысил
`scope.max_items`.

## Хранение и retention

- Образ: `arizephoenix/phoenix:19.0.0`.
- `PHOENIX_WORKING_DIR=/mnt/data` и volume `phoenix_data` сохраняют данные при
  пересоздании контейнера; удаление volume удаляет traces.
- `PHOENIX_DEFAULT_RETENTION_POLICY_DAYS=30`.
- `PHOENIX_TELEMETRY_ENABLED=false`.
- MCP включён, code mode выключен: `PHOENIX_ENABLE_MCP_CODE_MODE=false`.

Per-project production retention ещё не настроен.

## Автодиагностика и annotations

Evaluator проверяет runtime errors, unsupported claims, missing data, scope creep,
память и критиков. `EVALUATOR_TRACE_ATTRIBUTE_LIMIT=2500` ограничивает attributes
именно в контексте evaluator; это не гарантирует ограничение исходного OTLP span.

После оценки создаётся annotation с verdict, score, summary, run ID и списком issues.
`EVALUATOR_PHOENIX_ANNOTATIONS_ENABLED=false` отключает только запись annotation,
не саму оценку. Ошибка annotation фиксируется в metrics и не отменяет отчёт.

`GET /health` evaluator возвращает число оценок, опубликованных результатов и
`missing_traces` — runs, для которых trace не появился после всех попыток.

## Golden datasets и Experiments

В `agent-evaluator/scenarios.json` 24 сценария: 18 live и 6 фиксированных quality cases.
Проверяются завершение, Scope, Evidence, required tools и повторные запросы.

```bash
# Только локальная validation, без сети и runs
python agent-evaluator/experiments.py

# Получить или создать dataset, не запуская расследования
uv run --with 'arize-phoenix>=20,<21' agent-evaluator/experiments.py --upload-only

# Явно выполнить живые runs и записать experiment
uv run --with 'arize-phoenix>=20,<21' agent-evaluator/experiments.py --execute --variant baseline

# Живые runs на двух examples без записи experiment
uv run --with 'arize-phoenix>=20,<21' agent-evaluator/experiments.py --execute --dry-run 2 --variant smoke
```

`PHOENIX_URL` задаёт Phoenix, `AEGRA_API_URL` — стенд агента,
`EXPERIMENT_TIMEOUT_SECONDS=900` — максимум ожидания одного live-сценария. Для сравнения
моделей один и тот же dataset запускают после смены конфигурации с новым `--variant`.
Сам скрипт не меняет модель и не пересобирает контейнеры.

## Проверка интеграции

```bash
curl http://localhost:2026/health
curl http://localhost:6006
```

В Phoenix должны быть project `logicore-agent`, run/thread и role/scope/item metadata.
Живая OTLP-приёмка, annotations и persistence после пересоздания проверяются отдельно
по [ROADMAP.md](ROADMAP.md); unit-тесты их не заменяют.

## Официальные источники

- [Phoenix configuration](https://arize.com/docs/phoenix/self-hosting/configuration)
- [Docker deployment](https://arize.com/docs/phoenix/deployment/docker)
- [Metadata and tags](https://arize.com/docs/phoenix/tracing/how-to-tracing/customize-spans)
- [Datasets](https://arize.com/docs/phoenix/datasets-and-experiments/how-to-datasets)
- [Experiments](https://arize.com/docs/phoenix/datasets-and-experiments/how-to-experiments)
- [Remote MCP](https://arize.com/docs/phoenix/integrations/mcp)
