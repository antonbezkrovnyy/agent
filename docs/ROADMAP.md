# План доработок LogiCore Agent

Здесь находятся только **невыполненные** работы. Реализованные Scope, Planner,
метрики, MCP, артефакты, Phoenix annotations и golden-сценарии описаны в
[DEV.md](DEV.md), [OBSERVABILITY.md](OBSERVABILITY.md) и [EVALUATION.md](EVALUATION.md).

## 1. Живая приёмка и baseline

После пересборки выполнить на новых тредах, не редактируя код во время runs:

1. Широкое continuation: Discovery, не более двух проверок, отчёт и optional-suggestions.
2. Конкретная ветка: Targeted, посторонние аномалии не расширяют план.
3. SIEM-alert: Alert, сначала проверка алерта и только затем связанных сущностей.
4. Полный объём: `extract_data`, файл и корректный `complete/next_page`.
5. MCP: start → status → result → continue → cancel → restart; проверить
   `structuredContent`, свежие S3-ссылки и скачивание старого ZIP.
6. Phoenix: project, role/scope/item metadata, annotation и сохранение traces после
   пересоздания контейнера.
7. Отключить Phoenix на коротком run: Aegra продолжает работу, evaluator фиксирует
   missing trace, после восстановления повторная диагностика возможна.

Собрать baseline длительности, токенов, tool calls и повторных запросов по 24
golden-сценариям. После него определить SLO для quick и targeted; сейчас числовые
SLO не выдумываются. Общего watchdog длительности нет: soft/hard timeouts принадлежат
одному запуску роли и не должны превращаться в дедлайн всего расследования.

## 2. Phoenix для production

- Настроить per-project retention вместо только глобальных 30 дней.
- Проверить объём OTLP attributes на очень больших Evidence. Сейчас ограничен размер
  attributes в контексте evaluator, но не доказано ограничение исходного span в хранилище.
- Выполнять одинаковый dataset после смены модели/prompt version и сравнивать качество,
  стоимость и длительность; первая зафиксированная версия сравнения ещё не получена.
- Добавить read-only диагностику разрешённого кода с commit SHA только при конкретной
  необходимости. Без shell, секретов и самостоятельного изменения кода; intended behavior
  не заменяет Evidence/trace.

## 3. Внешние интерфейсы

A2A рассматривать только после появления конкретного потребителя поверх того же
investigation service. ACP, OIDC/multiuser и второй task manager не требуются.

## 4. Архитектурный контроль

- Не добавлять субагенты и уровни графа только из-за размера файла. Сначала выделять
  предметные модули и общие state-каналы; новый субграф нужен лишь при самостоятельных
  input/output, retry или concurrency-семантике.
- Не возвращать монолитный state в `agents.py`: использовать `EvidenceChannels`,
  `CaseChannels`, `MemoryChannels` и существующие delta-границы.
- Не создавать вторую СУБД или investigation engine, автоматическое переписывание skills,
  самостоятельное изменение кода агентом и бесконечное расширение на каждую находку.
