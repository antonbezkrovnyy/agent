FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/agent
WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.31" \
    "psycopg[binary]>=3.2" \
    "pydantic-settings>=2" \
    "langchain-openai>=1,<2"

COPY agent/config.py agent/memory_models.py agent/memory_store.py /app/agent/
COPY knowledge-service/*.py /app/
COPY knowledge-service/static /app/static
COPY silverbullet/seed /seed

EXPOSE 2028
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "2028"]
