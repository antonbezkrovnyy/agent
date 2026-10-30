"""SSE-клиент таймлайна Earth (Mars API): POST /mars/api/v1/events/timeline → text/event-stream.

События потока: init (скелет intervals), chunk (интервал со значением),
done (завершение), error (ошибка потока). На выходе — finished-payload прежней
формы {"status": "finished", "data": [бакеты {from, to, value}]}: timeline_flow
и merge потребляют без изменений (TD-0018, ревизия Mars API 2026-10-29).
"""

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx
from loguru import logger

from logicore_mcp.config import Settings

TIMELINE_ENDPOINT = "/mars/api/v1/events/timeline"

_EARTH_FMT = "%Y-%m-%d %H:%M:%S"


class TimelineTransportError(Exception):
    """Транспортная ошибка SSE-цикла таймлайна; конвертится в EarthError на уровне клиента (TD-0018)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _normalize_ts(value: Any) -> Any:
    """'2026-10-28T12:00:00Z' → '2026-10-28 12:00:00': прежняя форма finished-payload."""
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime(_EARTH_FMT)
    except ValueError:
        return value


async def fetch_timeline(
    http: httpx.AsyncClient,
    settings: Settings,
    token: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    """SSE-цикл: POST → init-скелет → chunk-значения → done (TD-0018, Mars API).

    Райзит TimelineTransportError при HTTP >= 400, событии error, закрытии
    потока до init и при исчерпании ws_wait-бюджета.
    """
    headers = {"Authorization": f"Bearer {token}"}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.ws_wait
    intervals: list[dict[str, Any]] | None = None
    seen_done = False

    def _on_event(name: str, data: str) -> None:
        nonlocal intervals, seen_done
        if not name:
            return
        payload: Any = json.loads(data) if data else {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        if name == "init":
            raw = payload.get("intervals")
            if not isinstance(raw, list):
                raise TimelineTransportError(f"init без intervals: {str(payload)[:150]}")
            intervals = [
                {
                    "from": _normalize_ts(item.get("from")),
                    "to": _normalize_ts(item.get("to")),
                    "value": item.get("value"),
                }
                for item in raw
                if isinstance(item, dict)
            ]
        elif name == "chunk":
            if intervals is None:
                raise TimelineTransportError("chunk до init")
            key = (_normalize_ts(payload.get("from")), _normalize_ts(payload.get("to")))
            for bucket in intervals:
                if (bucket["from"], bucket["to"]) == key:
                    bucket["value"] = payload.get("value")
                    break
            else:
                # chunk вне скелета — добавить, не терять данные
                intervals.append({"from": key[0], "to": key[1], "value": payload.get("value")})
        elif name == "error":
            message = payload.get("message")
            message = message if isinstance(message, str) else str(payload)
            raise TimelineTransportError(f"Ошибка потока таймлайна: {message[:200]}")
        elif name == "done":
            seen_done = True

    try:
        async with http.stream("POST", TIMELINE_ENDPOINT, json=request, headers=headers) as response:
            if response.status_code >= 400:
                text = (await response.aread()).decode("utf-8", "replace")
                raise TimelineTransportError(f"Таймлайн: HTTP {response.status_code}: {text[:200]}")
            stream = response.aiter_text()
            event_name = ""
            data_lines: list[str] = []
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimelineTransportError(f"Таймлайн не посчитался за {settings.ws_wait:.0f} с")
                try:
                    raw = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise TimelineTransportError(f"Таймлайн не посчитался за {settings.ws_wait:.0f} с") from None
                for line in raw.splitlines():
                    if line == "":
                        if event_name or data_lines:
                            _on_event(event_name, "\n".join(data_lines))
                            event_name, data_lines = "", []
                        continue
                    if line.startswith("event:"):
                        event_name = line.removeprefix("event:").strip()
                    elif line.startswith("data:"):
                        data_lines.append(line.removeprefix("data:").strip())
            if event_name or data_lines:
                _on_event(event_name, "\n".join(data_lines))
    except httpx.TimeoutException as exc:
        raise TimelineTransportError(f"Поток таймлайна недоступен (таймаут): {exc}") from None
    except httpx.HTTPError as exc:
        raise TimelineTransportError(f"Поток таймлайна недоступен: {exc}") from None

    if intervals is None:
        raise TimelineTransportError("Поток закрыт без init-скелета")
    if not seen_done:
        logger.warning("timeline sse: поток закрыт без done, берём накопленное состояние")
    logger.info("earth sse: endpoint=timeline buckets={}", len(intervals))
    return {"status": "finished", "data": intervals}
