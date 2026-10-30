"""Единый инстанс MCPServer — отдельный модуль, чтобы tools/resources импортировали без циклов."""

from mcp.server import MCPServer

mcp = MCPServer("logicore")


def _maybe_oauth() -> None:
    """Режим oauth (ADR-0011): включаем post-конфигурацией, если конфиг того требует.

    Ошибки валидации конфига здесь глотаются осознанно: импорт модуля обязан
    работать в тестах без env; громкий отказ даёт run() через load_settings().
    """
    from pydantic import ValidationError

    from logicore_mcp.config import load_settings

    try:
        settings = load_settings()
    except ValidationError:
        return
    if settings.auth == "oauth" and settings.issuer_url:
        from logicore_mcp.mcp.oauth import apply_oauth

        apply_oauth(mcp, settings.issuer_url)


_maybe_oauth()
