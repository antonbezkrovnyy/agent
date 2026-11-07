FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir \
    "duckdb>=1,<2" \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "pydantic>=2"

COPY app.py ./app.py

EXPOSE 9200
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9200"]
