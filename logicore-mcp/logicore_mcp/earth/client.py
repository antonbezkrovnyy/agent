"""Protocol-интерфейс Earth-клиента и httpx-реализация (TD-0002, TD-0005).

Whitelist эндпоинтов = методы клиента: login, search_events, group_by, field_columns (R-0002).
"""

import asyncio
from typing import Any, Protocol

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from logicore_mcp.config import Settings
from logicore_mcp.domain.errors import EarthError
from logicore_mcp.earth.auth import TokenCache
from logicore_mcp.earth.schemas import EventCardResponse, EventsResponse, build_single_params

LOGIN_ENDPOINT = "/api/v1/auth/login"
EVENTS_ENDPOINT = "/mars/api/v1/events"
EVENTS_SINGLE_ENDPOINT = "/mars/api/v1/events/single"
GROUPS_ENDPOINT = "/mars/api/v1/events/groups"
COUNT_ENDPOINT = "/mars/api/v1/events/count"
SETTINGS_ENDPOINT = "/mars/api/v1/events/settings"

EARTH_TIMEOUT_HINT = (
    "Сузьте окно или уточните фильтр: повтор того же запроса обречён. "
    "Для contains по message сами найдите максимальное окно, укладывающееся в таймаут: "
    "начните с окна ≤1 сутки и расширяйте вдвое за шаг — последовательно, не параллельным батчем; "
    "потолок 8 дней относится только к timeline."
)
EARTH_UNAVAILABLE_HINT = (
    "Earth перегружен или упал на запросе. Сузьте окно или уточните фильтр, затем повторите: "
    "для contains по message начните с окна ≤1 сутки и расширяйте вдвое за шаг — "
    "последовательно, не параллельным батчем."
)
EARTH_META_TIMEOUT_HINT = (
    "Метаданные схемы не зависят от окна/фильтра — «сузить окно» здесь не применимо. "
    "Повторите чтение позже: стенд мог быть перегружен волной запросов. "
    "Если события/группы при этом читаются, проблема локальна в columns-эндпоинте."
)


class UnauthorizedError(EarthError):
    """401 от Earth после повторного логина — сигнал наверх для конверта (TD-0002)."""


class EarthTimeoutError(EarthError):
    """Таймаут Earth как отдельный класс — триггер адаптивного дробления (TD-0029, PLAN-0009)."""


class EarthClient(Protocol):
    async def user_context(self) -> dict[str, Any]: ...

    def tenant_ids(self) -> list[int]: ...

    async def search_events(self, request: dict[str, Any]) -> EventsResponse: ...

    async def event_card(self, *, uuid: str, timestamp: str, tenant_ids: list[int]) -> EventCardResponse: ...

    async def group_by(self, request: dict[str, Any]) -> list[dict[str, Any]]: ...
    async def count_events(self, request: dict[str, Any]) -> int: ...

    async def timeline(self, request: dict[str, Any]) -> dict[str, Any]: ...

    async def field_columns(self) -> list[dict[str, Any]]: ...


def _map_auth_error(status: int, message: str) -> EarthError:
    if status == 401:
        return UnauthorizedError(
            "Авторизация Earth не удалась (401 после повторного логина)", hint="Проверьте LOGIQ_USER / LOGIQ_PASSWORD."
        )
    if status == 403:
        return EarthError(
            "Недостаточно прав учётной записи Earth", hint="Учётной записи нужны права на чтение событий."
        )
    return EarthError(f"Earth отклонил запрос: HTTP {status}", hint=None)


def _business_error(payload: dict[str, Any]) -> EarthError:
    message = payload.get("message") or "без сообщения"
    if "Ошибка получения данных" in str(message):
        return EarthError("Фильтр не поддержан или некорректен для Earth", hint=str(message))
    if "bad sql string" in str(message).lower():
        return EarthError(
            "MARS отклонил SQL/IQL-выражение",
            hint="Проверьте iql: это одно WHERE-условие без SELECT и ';'. Исправьте синтаксис и повторите.",
        )
    return EarthError(f"Ошибка Earth: {message}", hint=None)


def _single_key_error(payload: dict[str, Any]) -> EarthError:
    """422 карточки: детали по полям ключа из errors Earth — в hint (TD-0006)."""
    message = payload.get("message") or "неверный ключ карточки"
    errors = payload.get("errors")
    details: list[str] = []
    if isinstance(errors, dict):
        for field, messages in errors.items():
            if isinstance(messages, list):
                details.extend(f"{field}: {msg}" for msg in messages if isinstance(msg, str))
    return EarthError(f"Ошибка Earth: {message}", hint="; ".join(details) or None)


class _HttpTransport:
    """Единый httpx.AsyncClient на процесс + дисциплина TD-0002: таймауты, retry сетевых, 5xx без ретраев.

    Глобальный семафор concurrency 1 сериализует ВСЕ Earth-переходы (login, events,
    groups, count, timeline, columns) — очередь против параллельных вееров клиентов
    (TD-0028, PLAN-0009; пересмотр решения R-0024).
    """

    def __init__(self, client: httpx.AsyncClient, concurrency: int = 1) -> None:
        self._client = client
        self._gate = asyncio.Semaphore(concurrency)

    async def post_login(self, user: str, password: str) -> dict[str, Any]:
        async with self._gate:
            response = await self._request("POST", LOGIN_ENDPOINT, json={"login": user, "password": password})
            return self._payload(response)

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        token: str,
        json: dict[str, Any] | None = None,
        params: list[tuple[str, Any]] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Authorization": f"Bearer {token}"}
        async with self._gate:
            response = await self._request(method, endpoint, headers=headers, json=json, params=params)
        return response.status_code, self._payload(response)

    async def request_raw(
        self,
        method: str,
        endpoint: str,
        *,
        token: str,
        params: list[tuple[str, Any]] | None = None,
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {token}"}
        async with self._gate:
            response = await self._request(method, endpoint, headers=headers, params=params)
        if response.status_code >= 400:
            return response.status_code, self._payload(response)
        try:
            return response.status_code, response.json()
        except ValueError as exc:
            raise EarthError("Земля вернула не-JSON", hint=str(exc)) from exc

    @retry(
        retry=retry_if_exception_type((httpx.ConnectError, httpx.ReadError, httpx.WriteError)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, max=2),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        params: list[tuple[str, Any]] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, endpoint, headers=headers, json=json, params=params)
        except httpx.TimeoutException as exc:
            raise EarthTimeoutError("Earth временно недоступен (таймаут)", hint=EARTH_TIMEOUT_HINT) from exc
        if response.status_code >= 500:
            raise EarthError("Earth временно недоступен (5xx)", hint=EARTH_UNAVAILABLE_HINT)
        return response

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise EarthError("Земля вернула не-JSON", hint=str(exc)) from exc
        if isinstance(payload, dict):
            return payload
        return {"result": True, "data": payload, "message": ""}


class HttpEarthClient:
    """Реализация EarthClient: ленивый логин, один повтор по 401, конверт ошибок, журнал (TD-0002)."""

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = _HttpTransport(http_client or self._default_client(), concurrency=settings.earth_concurrency)
        self._auth = TokenCache(self._http, settings)

    def _default_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.url,
            verify=self._settings.tls_verify,
            timeout=httpx.Timeout(self._settings.timeout_read, connect=self._settings.timeout_connect),
        )

    async def aclose(self) -> None:
        await self._http._client.aclose()

    async def user_context(self) -> dict[str, Any]:
        await self._auth.token()
        return self._auth.user

    def tenant_ids(self) -> list[int]:
        return self._auth.tenant_ids()

    async def _authorized_call(
        self,
        method: str,
        endpoint: str,
        json: dict[str, Any] | None = None,
        params: list[tuple[str, Any]] | None = None,
    ) -> Any:
        token = await self._auth.token()
        status, payload = await self._http.request(method, endpoint, token=token, json=json, params=params)
        if status == 401:
            token = await self._auth.invalidate_and_relogin()
            status, payload = await self._http.request(method, endpoint, token=token, json=json, params=params)
            if status == 401:
                raise _map_auth_error(401, "")
        return status, payload

    async def search_events(self, request: dict[str, Any]) -> EventsResponse:
        status, payload = await self._authorized_call("POST", EVENTS_ENDPOINT, json=request)
        logger.info("earth call: endpoint=events status={} user={}", status, self._settings.user)
        if status in (400, 422):
            raise _business_error(payload if isinstance(payload, dict) else {})
        if isinstance(payload, dict) and payload.get("result") is False:
            raise _business_error(payload)
        if not isinstance(payload, dict):
            raise EarthError("Неожиданная форма ответа поиска", hint=None)
        return EventsResponse.model_validate(payload)

    async def event_card(self, *, uuid: str, timestamp: str, tenant_ids: list[int]) -> EventCardResponse:
        """Карточка события по ключу uuid+timestamp, GET /events/single (TD-0006)."""
        params = build_single_params(uuid=uuid, timestamp=timestamp, tenant_ids=tenant_ids)
        status, payload = await self._authorized_call("GET", EVENTS_SINGLE_ENDPOINT, params=params)
        logger.info("earth call: endpoint=single status={} user={}", status, self._settings.user)
        if status in (400, 422):
            raise _single_key_error(payload if isinstance(payload, dict) else {})
        if isinstance(payload, dict) and payload.get("result") is False:
            raise _business_error(payload)
        if not isinstance(payload, dict):
            raise EarthError("Неожиданная форма ответа карточки", hint=None)
        return EventCardResponse.model_validate(payload)

    async def group_by(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        status, payload = await self._authorized_call("POST", GROUPS_ENDPOINT, json=request)
        logger.info("earth call: endpoint=groups status={} user={}", status, self._settings.user)
        if status in (400, 422):
            raise _business_error(payload if isinstance(payload, dict) else {})
        if isinstance(payload, dict) and payload.get("result") is False:
            raise _business_error(payload)
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            raise EarthError("Неожиданная форма ответа groups", hint=None)
        return data

    async def count_events(self, request: dict[str, Any]) -> int:
        """Число событий окна под фильтром, POST /events/count (TD-0017)."""
        status, payload = await self._authorized_call("POST", COUNT_ENDPOINT, json=request)
        logger.info("earth call: endpoint=count status={} user={}", status, self._settings.user)
        if status in (400, 422, 500):
            raise _business_error(payload if isinstance(payload, dict) else {})
        if isinstance(payload, dict) and payload.get("result") is False:
            raise _business_error(payload)
        data = payload.get("data") if isinstance(payload, dict) else None
        count = data.get("count") if isinstance(data, dict) else None
        if not isinstance(count, int):
            raise EarthError("Неожиданная форма ответа count", hint=None)
        return count

    async def timeline(self, request: dict[str, Any]) -> dict[str, Any]:
        """Гистограмма окна: SSE-поток Mars API /events/timeline (TD-0018, ревизия 2026-10-29)."""
        from logicore_mcp.earth.timeline_sse import TimelineTimeoutError, TimelineTransportError, fetch_timeline

        token = await self._auth.token()
        try:
            async with self._http._gate:
                result = await fetch_timeline(self._http._client, self._settings, token, request)
        except TimelineTimeoutError as exc:
            raise EarthTimeoutError("Таймлайн Earth не успел завершиться", hint=EARTH_TIMEOUT_HINT) from exc
        except TimelineTransportError as exc:
            raise EarthError(exc.message, hint="Таймлайн Earth недоступен; объёмы — count_events.") from exc
        logger.info("earth call: endpoint=timeline status=200 user={}", self._settings.user)
        return result

    async def field_columns(self) -> list[dict[str, Any]]:
        """Словарь полей: Mars API GET /events/settings → data.columns (ревизия 2026-10-29)."""
        token = await self._auth.token()
        try:
            status, payload = await self._http.request_raw("GET", SETTINGS_ENDPOINT, token=token)
            if status == 401:
                token = await self._auth.invalidate_and_relogin()
                status, payload = await self._http.request_raw("GET", SETTINGS_ENDPOINT, token=token)
        except EarthTimeoutError as exc:
            # metadata-эндпоинт без окна: оконный hint неприменим (TD-0002 рев. 2026-08-28, PLAN-0014 п. 1.3)
            raise EarthTimeoutError("Метаданные схемы недоступны (таймаут)", hint=EARTH_META_TIMEOUT_HINT) from exc
        except EarthError as exc:
            if exc.hint in (EARTH_TIMEOUT_HINT, EARTH_UNAVAILABLE_HINT):
                raise EarthError(str(exc), hint=EARTH_META_TIMEOUT_HINT) from exc
            raise
        logger.info("earth call: endpoint=settings status={} user={}", status, self._settings.user)
        if status >= 400:
            raise _map_auth_error(status, str(payload))
        if isinstance(payload, dict) and payload.get("result") is False:
            raise _business_error(payload)
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, dict):
            data = data.get("columns")
        if not isinstance(data, list):
            raise EarthError("Неожиданная форма ответа settings", hint=None)
        return data
