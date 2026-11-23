import os
from datetime import datetime

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel


class Response(BaseModel):
    text: str


@tool
def current_time() -> str:
    """Возвращает текущие локальные дату и время."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def make_model() -> ChatDeepSeek:
    load_dotenv()
    return ChatDeepSeek(
        model=os.getenv("PARSER_AI_MODEL", "gpt-jet"),
        base_url=os.getenv("PARSER_AI_BASE_URL", "https://api.neuraldeep.ru/v1"),
        api_key=os.getenv("PARSER_AI_API_KEY"),
        temperature=0,
        timeout=60,
    )


def main() -> None:
    model = make_model()
    agent = create_agent(model, tools=[current_time])
    respond = model.with_structured_output(Response, method="json_mode")
    history = []
    print("Диалог начат. /new — новый диалог, /exit — выход.")

    while True:
        try:
            text = input("\nВы: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == "/exit":
            break
        if text == "/new":
            history.clear()
            continue
        if not text:
            continue

        history.append(HumanMessage(content=text))
        result = agent.invoke({"messages": history})
        for message in result["messages"]:
            if not isinstance(message, AIMessage):
                continue
            reasoning = message.additional_kwargs.get("reasoning_content")
            if reasoning:
                print(f"\nReasoning: {reasoning}")
            for call in message.tool_calls:
                print(f"Tool: {call['name']} {call['args']}")

        response = respond.invoke([
            SystemMessage(
                content='Верни итоговый ответ пользователю только как JSON-объект '
                '{"text": "текст ответа"}. Не добавляй других полей.'
            ),
            *result["messages"],
        ])
        history.append(AIMessage(content=response.text))
        print(f"Агент: {response.text}")


if __name__ == "__main__":
    main()
