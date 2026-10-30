"""Приёмник нативного аномали-вебхука Логикора → запуск расследования в agra.

Универсальный контракт: принимается ЛЮБОЙ JSON-объект как payload алерта.
- callback_url (варианты callbackUrl/callback) в payload — куда агент доставит
  отчёт (тул send_report); иначе дефолт из env DEFAULT_CALLBACK_URL; иначе
  доставки не будет (отчёт останется файлом в тред/UI).
- Тред детерминирован по hash payload (повтор алерта → тот же тред);
  run стартует on_disconnect=continue, relay отвечает сразу.
"""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

AGRA_API_URL = os.environ.get("AGRA_API_URL", "http://aegra:2026")
AGRA_ASSISTANT = os.environ.get("AGRA_ASSISTANT", "agent")
RELAY_TOKEN = os.environ.get("RELAY_TOKEN", "").strip()
DEFAULT_CALLBACK_URL = os.environ.get("DEFAULT_CALLBACK_URL", "").strip()

app = FastAPI(title="anomaly-relay", version="1.0.0")

# agra 0.11.0 не применяет multitask_strategy (принимает, но 409 не отдаёт) —
# дедупликация повторных алертов сделана здесь: busy-проверка + сериализация запросов
_ANOMALY_LOCK = asyncio.Lock()


async def check_auth(request: Request) -> None:
    """Bearer-токен, если задан RELAY_TOKEN (внешний звонок с сервера Логикора)."""
    if not RELAY_TOKEN:
        return
    if request.headers.get("Authorization", "") != f"Bearer {RELAY_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _extract_callback(payload: object) -> str | None:
    if isinstance(payload, dict):
        for key in ("callback_url", "callbackUrl", "callback"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return DEFAULT_CALLBACK_URL or None


def _thread_id_for(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return "lw-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _human_prompt(payload: object) -> str:
    body = (
        json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        if isinstance(payload, dict)
        else str(payload)
    )
    return (
        "ЗАДАЧА РАССЛЕДОВАНИЯ (автоматический вебхук Логикора):\n"
        f"{body}\n\n"
        "Проведи полное расследование инструментами (server_time, count_events, "
        "timeline, top_values, search_events и др.), определи причину аномалии и "
        "доставь готовый отчёт через callback — эндпоинт уже задан в данных запуска."
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "callback_default": bool(DEFAULT_CALLBACK_URL), "token": bool(RELAY_TOKEN)}


@app.post("/anomaly", dependencies=[Depends(check_auth)])
async def anomaly(request: Request) -> JSONResponse:
    raw = await request.body()
    try:
        payload: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = raw.decode("utf-8", errors="replace")  # не-JSON тоже расследуем как текст

    callback_url = _extract_callback(payload)
    thread_id = _thread_id_for(payload)

    async with _ANOMALY_LOCK:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{AGRA_API_URL}/threads",
                json={
                    "threadId": thread_id,
                    "ifExists": "do_nothing",
                    "metadata": {
                        "kind": "logicore-webhook",
                        "received_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    },
                },
            )
            if r.status_code not in (200, 201):
                return JSONResponse(
                    {"ok": False, "error": f"thread create: {r.status_code} {r.text[:300]}"},
                    status_code=502,
                )

            # Дедупликация: по треде уже идёт расследование — отчёт придёт от него
            if (s := await client.get(f"{AGRA_API_URL}/threads/{thread_id}")).status_code == 200:
                if s.json().get("status") == "busy":
                    return JSONResponse(
                        {
                            "ok": True,
                            "thread_id": thread_id,
                            "callback": callback_url,
                            "note": "run уже выполняется для этого треда",
                        }
                    )

            run_input: dict = {"messages": [{"role": "human", "content": _human_prompt(payload)}]}
            if callback_url:
                run_input["callback_url"] = callback_url
            r = await client.post(
                f"{AGRA_API_URL}/threads/{thread_id}/runs",
                json={
                    "assistant_id": AGRA_ASSISTANT,
                    "input": run_input,
                    "on_disconnect": "continue",
                },
            )
            if r.status_code in (200, 202):
                return JSONResponse({"ok": True, "thread_id": thread_id, "callback": callback_url})
            if r.status_code == 409:
                return JSONResponse(
                    {
                        "ok": True,
                        "thread_id": thread_id,
                        "callback": callback_url,
                        "note": "run уже выполняется для этого треда",
                    }
                )
            return JSONResponse(
                {"ok": False, "error": f"run create: {r.status_code} {r.text[:300]}"},
                status_code=502,
            )
