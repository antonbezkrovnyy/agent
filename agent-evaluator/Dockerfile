FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn>=0.31" \
    "httpx>=0.27" \
    "psycopg[binary]>=3.2" \
    "pydantic>=2" \
    "pydantic-settings>=2" \
    "langchain-core>=1,<2" \
    "langchain-openai>=1,<2" \
    "arize-phoenix>=20,<21"

COPY agent-evaluator/*.py ./
COPY agent-evaluator/scenarios.json ./scenarios.json

EXPOSE 9200
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9200"]
