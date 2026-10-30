# anomaly-relay

Приёмник нативного аномали-вебхука Логикора: `POST /anomaly` → создание треда в agra →
запуск расследования (граф `agent`) → агент доставляет отчёт на `callback_url`
(тул `send_report`), параллельно прикрепляя файл к ответу в UI.

## Контракт

`POST http://<host>:2025/anomaly` (JSON, любой объект — поля Логикора не маппятся,
payload целиком уходит в задачу расследования):

```json
{
  "any_logicore_fields": "...",
  "callback_url": "https://…/receive-report"
}
```

- `callback_url` (варианты `callbackUrl`, `callback`) — куда агент POST-ит готовый отчёт:
  JSON `{event: "investigation_report", title, filename, markdown, sent_at, thread_id}`.
  Если в payload нет — дефолт из env `DEFAULT_CALLBACK_URL`; если и там нет — отчёт
  остаётся файлом в треде (UI), доставки не будет.
- Ответ: `202 {"ok": true, "thread_id": "lw-…", "callback": "…"}`. Повторный алерт с
  тем же payload → тот же тред; если run уже идёт — `202 … "note": "run уже выполняется"`.
- `GET /healthz` — здоровье + конфиг.

## Настройка (env)

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `AGRA_API_URL` | `http://aegra:2026` | agra внутри docker-сети |
| `AGRA_ASSISTANT` | `agent` | assistant для run'ов |
| `RELAY_TOKEN` | — | Bearer-токен на входе (для внешнего звона Логикора); пустой — открыто (dev) |
| `DEFAULT_CALLBACK_URL` | — | callback, если его нет в payload |

## Запуск

```
curl -X POST http://localhost:2025/anomaly \
  -H "Content-Type: application/json" \
  -d '{"alert": "spike events", "callback_url": "https://…/cb"}'
```
