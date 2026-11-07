---
title: LogiCore Knowledge
status: published
---

# LogiCore Knowledge

Рабочее пространство знаний и отчётов LogiCore. Это не чат агента.

- [[Runbooks/Stream Stopped|Runbook: прекращение потока]]
- [[Entities/VPN-01|Пример страницы сущности]]
- [[Reports|Отчёты расследований]]

## Память агента

[Открыть память](http://localhost:2028/memory)

Факты хранятся в `agent-memory-db`. Редактор изменяет их через Memory API и обновляет семантический индекс. SilverBullet не создаёт вторую копию памяти.

## Как агент использует страницы

Агент вызывает `wiki_search` и `wiki_read`, получает небольшие карточки и разделы. Содержимое страниц направляет работу, но не становится Evidence. Выводы подтверждаются свежими запросами к LogiCore.
