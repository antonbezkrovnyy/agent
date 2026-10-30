import asyncio
import sys
from langgraph_sdk import get_client

# URL внутреннего сервиса Aegra в Docker-сети
AEGRA_URL = "http://aegra:2026"

async def wait_for_aegra(client):
    """Ждем, пока сервер Aegra полностью запустится и будет готов принимать запросы."""
    print("⏳ Ожидание запуска сервера Aegra...")
    while True:
        try:
            await client.assistants.search()
            print("✅ Сервер Aegra готов!")
            break
        except Exception:
            await asyncio.sleep(2)

async def register_agents():
    client = get_client(url=AEGRA_URL)
    await wait_for_aegra(client)

    # ==========================================
    # ЗДЕСЬ ВЫ РЕГИСТРИРУЕТЕ СВОИХ АГЕНТОВ
    # ==========================================
    
    # 1. Первый агент (наш Simple Graph)
    # graph_id должен соответствовать тому, как граф назван внутри системы. 
    # По умолчанию Aegra ищет граф с именем "agent" или мы можем указать путь.
    try:
        await client.assistants.create(
            graph_id="agent",  # Имя графа
            config={"configurable": {"thread_id": "default"}},
            metadata={"description": "Мой первый динамический агент из Module 1"}
        )
        print("✅ Агент 'agent' успешно зарегистрирован!")
    except Exception as e:
        print(f"⚠️ Агент 'agent' уже существует или ошибка: {e}")

    # ==========================================
    # В БУДУЩЕМ: Добавьте второго агента сюда:
    # await client.assistants.create(
    #     graph_id="agent_v2",
    #     config={"configurable": {"thread_id": "default"}},
    #     metadata={"description": "Мой второй агент"}
    # )
    # ==========================================

if __name__ == "__main__":
    asyncio.run(register_agents())