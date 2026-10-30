FROM python:3.11-slim

WORKDIR /app/agent

# Устанавливаем зависимости для агента и SDK
RUN pip install --no-cache-dir langgraph typing-extensions langgraph-sdk

# Копируем код
COPY . .

# При запуске регистрируем граф в Aegra (и держим контейнер живым, если нужно)
CMD ["python", "register.py"]