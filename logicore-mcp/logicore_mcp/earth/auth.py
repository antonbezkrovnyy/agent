"""Ленивый логин, кэш токена in-memory, релогин по 401 (TD-0002, R-0005)."""

import asyncio
from typing import Any, Protocol

from loguru import logger

from logicore_mcp.config import Settings
from logicore_mcp.domain.errors import EarthError


class LoginTransport(Protocol):
    """Транспорт логина: единственная сетевая точка TokenCache (тестируется MockTransport)."""

    async def post_login(self, user: str, password: str) -> dict[str, Any]: ...


class TokenCache:
    """Токен Earth на процесс: лениво, реактивная инвалидация по 401, без расчётного TTL (TD-0002).

    Пользовательский контекст (тенанты) кэшируется вместе с токеном одного логина.
    """

    def __init__(self, transport: LoginTransport, settings: Settings) -> None:
        self._transport = transport
        self._settings = settings
        self._token: str | None = None
        self._user: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    @property
    def user(self) -> dict[str, Any]:
        if self._user is None:
            raise EarthError("Контекст пользователя не загружен", hint="Сначала token() — логин ленивый (TD-0002).")
        return self._user

    def tenant_ids(self) -> list[int]:
        return self._scope_tenant_ids(self._account_tenant_ids(self.user))

    def _scope_tenant_ids(self, account: list[int]) -> list[int]:
        """Фильтр тенантов конфигом LOGIQ_TENANT_IDS (ADR-0007): пусто — все тенанты учётки."""
        configured = self._settings.tenant_ids
        if not configured:
            return account
        allowed = [t for t in account if t in configured]
        dropped = [t for t in configured if t not in account]
        if dropped:
            logger.warning("tenant scope: конфигурация содержит недоступные учётке тенанты: {}", dropped)
        return allowed

    async def token(self) -> str:
        async with self._lock:
            if self._token is None:
                await self._login()
            return self._token  # type: ignore[return-value]

    async def invalidate_and_relogin(self) -> str:
        """Сброс после 401 и повторный логин; вызывается вне lock (TD-0002: один повтор)."""
        async with self._lock:
            self._token = None
            self._user = None
            await self._login()
            return self._token  # type: ignore[return-value]

    async def _login(self) -> None:
        payload = await self._transport.post_login(self._settings.user, self._settings.password)
        data = payload.get("data") or {}
        token = data.get("token")
        if payload.get("result") is False or not isinstance(token, str) or not token:
            message = payload.get("message") or "пустой токен в ответе"
            raise EarthError(
                f"Логин Earth не удался: {message}",
                hint="Проверьте LOGIQ_USER / LOGIQ_PASSWORD.",
            )
        self._token = token
        self._user = data.get("user") or {}
        account = self._account_tenant_ids(self._user)
        scoped = self._scope_tenant_ids(account)
        if self._settings.tenant_ids and not scoped:
            raise EarthError(
                "LOGIQ_TENANT_IDS не пересекается с тенантами учётки",
                hint=(
                    f"Учётке доступны: {account}; конфиг: {list(self._settings.tenant_ids)} (ADR-0007). "
                    "Пустая выдача по misconfig была бы тихой — поэтому ошибка."
                ),
            )
        logger.info("earth login ok: user={} tenants={}", self._settings.user, len(account))

    @staticmethod
    def _account_tenant_ids(user: dict[str, Any]) -> list[int]:
        return [int(t["id"]) for t in user.get("tenants") or [] if isinstance(t, dict) and "id" in t]
