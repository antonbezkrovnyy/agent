FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Зависимости MCP-сервера logicore_mcp (список из pyproject mcp-logicore)
RUN pip install --no-cache-dir \
    "mcp[cli]>=2,<3" \
    "pydantic>=2" \
    "pydantic-settings>=2" \
    "httpx>=0.27" \
    "loguru>=0.7" \
    "tenacity>=8" \
    "cachetools>=5"

# Пакет MCP-сервера (build-контекст — ./logicore-mcp)
COPY logicore_mcp ./logicore_mcp

EXPOSE 9000

# Запуск: python -m logicore_mcp (входная точка — logicore_mcp/__main__.py)
CMD ["python", "-m", "logicore_mcp"]
