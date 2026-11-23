import argparse
import json
import os

from dotenv import load_dotenv
import httpx2
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_deepseek import ChatDeepSeek
from pydantic import ValidationError

from prompts import RESEARCH_PROMPT, SYSTEM_PROMPT
from schemas import Response
import storage
from tools import TOOLS, error_response, handle_tool_errors, validation_error_response
from validation import check_response


def make_model() -> ChatDeepSeek:
    load_dotenv()
    return ChatDeepSeek(
        model=os.getenv("PARSER_AI_MODEL", "gpt-jet"),
        base_url=os.getenv("PARSER_AI_BASE_URL", "https://api.neuraldeep.ru/v1"),
        api_key=os.getenv("PARSER_AI_API_KEY"),
        temperature=0,
        timeout=httpx2.Timeout(None),
        streaming=True,
    )


def run_step(
    agent,
    history: list[BaseMessage],
    raw: str,
    show_response: bool = True,
    show_tools: bool = True,
    compact_tools: bool = False,
) -> Response:
    messages = history
    while True:
        final_state = None
        for chunk in agent.stream(
            {"messages": messages},
            stream_mode=["updates", "values"],
            version="v2",
        ):
            if chunk["type"] == "values":
                final_state = chunk["data"]
                continue
            for source, update in chunk["data"].items():
                if source not in ("model", "tools"):
                    continue
                message = update["messages"][-1]
                if isinstance(message, AIMessage) and show_tools:
                    for call in message.tool_calls:
                        details = "" if compact_tools else f" {call['args']}"
                        print(f"Tool: {call['name']}{details}", flush=True)
                elif isinstance(message, ToolMessage) and show_tools and not compact_tools:
                    print(f"Tool result: {message.content}", flush=True)

        if final_state is None:
            raise RuntimeError("Агент не вернул финальное состояние")
        content = next(
            (
                message.content
                for message in reversed(final_state["messages"])
                if isinstance(message, AIMessage) and message.content
            ),
            None,
        )
        try:
            response = Response.model_validate_json(content or "")
        except ValidationError as error:
            messages = list(final_state["messages"])
            messages.append(HumanMessage(content=validation_error_response(
                error,
                recovery=(
                    "Не повторяй исследование и не вызывай инструменты. Исправь только финальный "
                    "Response и верни полный JSON-объект без markdown и пояснений."
                ),
            )))
            continue
        problem = check_response(response, raw)
        if problem:
            messages = list(final_state["messages"])
            messages.append(HumanMessage(content=error_response(
                problem["error"],
                problem["message"],
                recovery=problem["recovery"],
                details=problem["details"],
            )))
            continue
        history.append(AIMessage(content=response.model_dump_json()))
        if show_response:
            print(f"Агент: {response.model_dump_json(indent=2)}")
        return response


def run_observation(
    agent,
    raw: str,
    show_response: bool = True,
    show_tools: bool = True,
    compact_tools: bool = False,
) -> tuple[Response, int]:
    response = run_step(
        agent,
        [HumanMessage(content=RESEARCH_PROMPT.format(log=raw))],
        raw,
        show_response=show_response,
        show_tools=show_tools,
        compact_tools=compact_tools,
    )
    saved = storage.save_research(response.model_dump(mode="json"))
    print(f"Research result saved: id={saved['id']}")
    return response, saved["id"]


def make_agent():
    return create_agent(
        make_model(),
        tools=TOOLS,
        middleware=[handle_tool_errors],
        system_prompt=SYSTEM_PROMPT + json.dumps(Response.model_json_schema(), ensure_ascii=False),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Исследование одной RAW-строки лога.")
    parser.add_argument("raw", nargs="?", help="RAW-строка; без аргумента запускается диалог.")
    args = parser.parse_args()

    agent = make_agent()
    if args.raw is not None:
        run_observation(agent, args.raw)

    print("\nДиалог начат. Каждая строка — независимое наблюдение. /exit — выход.")
    while True:
        try:
            raw = input("\nRAW: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if raw == "/exit":
            break
        if raw:
            run_observation(agent, raw)


if __name__ == "__main__":
    main()
