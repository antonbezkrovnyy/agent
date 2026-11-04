FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Зависимости пакета agent/ + ag-ui-langgraph (AG-UI протокол поверх LangGraph).
# Версии langchain/langgraph согласованы с тестовым пином репозитория.
RUN pip install --no-cache-dir \
    "ag-ui-langgraph[fastapi]" \
    "langchain==1.3.10" \
    "langgraph==1.2.16" \
    "langchain-openai==1.3.2" \
    "langchain-mcp-adapters==0.2.1" \
    "pydantic-settings>=2" \
    "httpx>=0.27" \
    "tenacity>=8" \
    "loguru>=0.7" \
    "psycopg[binary]" \
    "mcp[cli]==1.30.0" \
    "uvicorn>=0.30"

COPY agent/ ./agent/
COPY agui-agent/app.py ./app.py

EXPOSE 2027

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "2027"]
