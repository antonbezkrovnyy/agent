"""Рантайм сборки: settings, ленивый Earth-клиент, кэш словаря полей (ADR-0001, TD-0002)."""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from loguru import logger

from logicore_mcp.config import Settings, load_settings
from logicore_mcp.domain.filters import FieldSchema
from logicore_mcp.domain.ratelimit import RollingWindowLimiter
from logicore_mcp.earth.client import EarthClient, HttpEarthClient
from logicore_mcp.earth.scheduler import ScheduledEarthClient

_settings: Settings | None = None
_client: EarthClient | None = None
_fields_schema: dict[str, FieldSchema] | None = None
_rate_limiter: RollingWindowLimiter | None = None
_user_clients: dict[str, EarthClient] = {}


def register_user_client(user_id: str, client: EarthClient) -> None:
    """Пер-пользовательский Earth-клиент после успешного логина (ADR-0011).

    Живёт в памяти процесса: рестарт обнуляет — пользователь перелогинится.
    """
    _user_clients[user_id] = client


def _current_subject() -> str | None:
    """Пользователь из access-токена текущего запроса (oauth, ADR-0011); вне запроса — None."""
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
    except Exception:
        return None
    return token.subject if token is not None else None


def _setup_logging(log_file: str | None) -> None:
    """stderr — основной сток (ADR-0002); LOGIQ_LOG_FILE — дубль в файл для ручной
    отладки (ревизия ADR-0002 от 2026-08-25): stdout по-прежнему чист."""
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(str(path), level="INFO", rotation="10 MB", retention=5, encoding="utf-8")


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
        _setup_logging(_settings.log_file)
    return _settings


@asynccontextmanager
async def use_client() -> AsyncIterator[EarthClient]:
    """Ленивый Earth-клиент; логин при первом вызове (TD-0002).

    Возвращается планировщик: очередь TD-0028 (внутри HttpEarthClient) +
    адаптивное дробление окон TD-0029 (PLAN-0009).
    LOGIQ_FAKE_EARTH — red-team профиль: вместо планировщика отдаётся
    детерминированный фейк поверх фикстуры (TD-0040).
    OAuth (ADR-0011): пользователь с валидным access-токеном получает свой
    клиент из пер-пользовательского кэша; остальные — общий синглтон.
    """
    global _client
    subject = _current_subject()
    if subject is not None and subject in _user_clients:
        yield _user_clients[subject]
        return
    if _client is None:
        config = settings()
        if config.fake_earth:
            from logicore_mcp.redteam import RedTeamEarthClient, load_fixture

            _client = RedTeamEarthClient(load_fixture(config.fake_earth))
        else:
            _client = ScheduledEarthClient(HttpEarthClient(config), config)
    yield _client


async def schema_fields_catalog() -> dict[str, FieldSchema]:
    """Словарь полей Earth → схема для валидации фильтров; кэш на процесс (TD-0002 рев. 2026-08-28, R-0004).

    Метаданные стенда статичны (развитие Земли остановлено, R-0001): снимок
    фиксируется при первом чтении и живёт до перезапуска процесса.
    """
    global _fields_schema
    if _fields_schema is not None:
        return _fields_schema
    async with use_client() as client:
        columns = await client.field_columns()
    schema = {
        str(col.get("name")): FieldSchema(name=str(col.get("name")), type=str(col.get("type", "")))
        for col in columns
        if col.get("name")
    }
    _fields_schema = schema
    logger.info("schema loaded: {} fields", len(schema))
    return schema


def fields_schema_sync() -> dict[str, FieldSchema] | None:
    return _fields_schema


def rate_limiter() -> RollingWindowLimiter | None:
    """Лимитер вызовов инструментов на процесс; None — выключен конфигом (TD-0039)."""
    global _rate_limiter
    config = settings()
    if config.rate_limit_calls <= 0:
        return None
    if _rate_limiter is None:
        _rate_limiter = RollingWindowLimiter(config.rate_limit_calls, config.rate_limit_window_s)
    return _rate_limiter
