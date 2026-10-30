# mcp_client.py
"""MCP-клиент для Логикора.

Сервер работает в отдельном контейнере logicore-mcp (docker-compose,
транспорт streamable-http + bearer-токен, ADR-0010). Адрес и токен —
из .env (12-Factor App).
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import BaseTool
from config import Settings

settings = Settings()

# Конфигурация MCP-сервера (12-Factor App)
_client = MultiServerMCPClient(
    {
        "logicore": {
            "transport": "http",
            "url": settings.logicore_mcp_url,
            "headers": {"Authorization": f"Bearer {settings.logicore_mcp_token}"},
        }
    }
)

_mcp_tools: list[BaseTool] | None = None


def get_mcp_tools() -> list[BaseTool]:
    """Синхронная функция для получения инструментов.

    Вызывается один раз в build_graph(). client.get_tools() асинхронный:
    в момент импорта в Aegra работающего цикла нет — крутим asyncio.run;
    если цикл уже есть (на всякий случай) — отдельный поток.
    Каждый последующий вызов инструмента сам открывает новую MCP-сессию.
    """
    global _mcp_tools
    if _mcp_tools is None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            _mcp_tools = asyncio.run(_client.get_tools())
        else:
            with ThreadPoolExecutor(max_workers=1) as pool:
                _mcp_tools = pool.submit(asyncio.run, _client.get_tools()).result()
    return _mcp_tools
