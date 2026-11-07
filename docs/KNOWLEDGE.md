# SilverBullet и память LogiCore

SilverBullet используется как рабочее пространство знаний, карточек отчётов и редактирования памяти. Чат агента в него не встраивается: диалог остаётся в Agent Chat UI.

## Компоненты

- `silverbullet` — Markdown-страницы, runbook’и, сущности и карточки отчётов, порт 3002.
- `knowledge-service` — индексация страниц, Wiki/Memory API и HTML-редактор памяти, порт 2028.
- `agent-memory-db` — единственный источник истины для Context Facts и производный pgvector-индекс страниц.
- S3 — исходные Markdown, ZIP и датасеты. SilverBullet хранит только карточки и ссылки.

```text
Markdown / Reports
  → knowledge-service
  → agent_memory.wiki_pages + wiki_sections + embeddings
  → wiki_search / wiki_read в Scout, Investigator и Reporter

SilverBullet Memory Panel
  → Memory API
  → agent_memory.context_facts + embeddings
```

## Запуск

```bash
docker compose up -d --build silverbullet knowledge-service aegra
curl http://localhost:2028/health
```

Открыть:

- SilverBullet: http://localhost:3002;
- редактор памяти: http://localhost:2028/memory;
- стартовая страница `Home` содержит ссылку на редактор памяти.

Стартовые страницы копируются из `silverbullet/seed` только если их ещё нет в volume. Изменения пользователя в `silverbullet_data` при следующих запусках не перезаписываются. `spaces.json` задаёт space `LogiCore` с `folder=/space`; учётные данные в seed не входят.

## Wiki API

- `GET /wiki/search?query=...&limit=3` — компактные карточки с ID, revision, summary и разделом;
- `GET /wiki/read?page_id=...&section=...` — страница или один раздел, максимум 12000 символов;
- `POST /reindex` — немедленная переиндексация;
- `GET /health` — число страниц, ожидающих embeddings и последняя ошибка.

Индексация выполняется каждые пять секунд. Страницы со `status: published` разбиваются на заголовки и получают embeddings в существующей `agent-memory-db`. Если embedding недоступен, страница остаётся в БД со `index_status=pending`, а поиск деградирует в обычный текстовый. Вызовы embeddings ограничены `KNOWLEDGE_TIMEOUT_SECONDS` и не имеют клиентских retry.

Содержимое SilverBullet — методический контекст, а не Evidence. Агент получает только небольшие карточки и разделы, а выводы подтверждает свежими запросами LogiCore.

## Редактирование памяти

Редактор позволяет:

- искать факты по смыслу и тексту;
- создавать и исправлять факты;
- менять срок актуальности;
- закреплять и исключать записи;
- видеть revision и `index_status`.

`PUT /memory/facts/{id}` требует `base_revision`: одновременные изменения не перезаписываются молча. Исправление создаёт новую revision и `superseded_by`, а не переписывает Evidence или историю. Pin/exclusion меняет только metadata и не пересчитывает embedding. Исправленный текст получает новый embedding; при ошибке он помечается `pending`.

Основные endpoints:

- `GET/POST /memory/facts`;
- `GET/PUT /memory/facts/{id}`;
- `POST /memory/facts/{id}/controls` с `base_revision` и `pinned` или `excluded`.

Excluded-записи не попадают в активный retrieval. Режим рассчитан на мягкое исключение с сохранением аудита, а не на безвозвратное удаление чувствительных данных. Для production hard-delete и retention требуются отдельно.

## Карточки отчётов

После создания Markdown/ZIP `agent/artifacts.py` вызывает `POST /reports/publish`. В `Reports/<case-id>` записываются:

- краткий вывод и verification status;
- case/run ID и saved_at;
- ссылки через `agui-agent/artifacts/<key>` на файлы в S3;
- content hashes остаются в исходных артефактах.

Публикация идемпотентно обновляет карточку дела, но не перезаписывает сами неизменяемые S3-объекты. Недоступность knowledge-service не отменяет расследование и сохранение отчёта.

## Настройки

- `KNOWLEDGE_SERVICE_URL` — URL для agent tools и publisher, пусто = интеграция отключена;
- `KNOWLEDGE_TIMEOUT_SECONDS=5` — HTTP и embeddings timeout;
- `KNOWLEDGE_SPACE_DIR=/space` — общий volume;
- `KNOWLEDGE_SEED_DIR=/seed` — стартовые страницы;
- `KNOWLEDGE_REINDEX_SECONDS=5` — период индексации;
- `KNOWLEDGE_AGUI_PUBLIC_URL=http://localhost:2027` — публичный resolver артефактов.

## Ограничения

Текущий anonymous-режим предназначен только для локальной разработки. Для совместной работы необходимы SSO, tenant-scopes, проверка прав в Memory/Wiki API и sanitized-публикация отчётов. SilverBullet plugs и Space Lua редактируются только администраторами; агент произвольные страницы и исполняемую конфигурацию не изменяет.
