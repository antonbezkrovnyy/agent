FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir \
    "mcp[cli]>=2,<3" \
    "httpx>=0.27" \
    "pydantic-settings>=2"

COPY agent-mcp/app.py ./app.py
COPY agent/artifact_store.py ./artifact_store.py
COPY agent/config.py ./config.py

EXPOSE 9100
CMD ["python", "app.py"]
