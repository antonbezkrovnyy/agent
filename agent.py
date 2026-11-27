import argparse
import json
import os

from dotenv import load_dotenv
import httpx2
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_deepseek import ChatDeepSeek
from pydantic import ValidationError

import feedback
from prompts import (
    RESEARCH_BATCH_PROMPT,
    RESEARCH_BATCH_SYSTEM_PROMPT,
    RESEARCH_PROMPT,
    MP_TEXT_TARGET_PROMPT,
    SYSTEM_PROMPT,
)
from schemas import ResearchBatch, ResearchBatchResponse, Response
import storage
from tools import MP_TEXT_TOOLS, TOOLS, error_response, handle_tool_errors, validation_error_response
from validation import check_batch_response, check_response


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
    runner_feedback_ids = []
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
            recovery = (
                "Не повторяй исследование и не вызывай инструменты. Исправь только финальный "
                "Response и верни полный JSON-объект без markdown и пояснений."
            )
            reported = feedback.runner_problem(
                agent="parser",
                category="invalid_response",
                message="Parser-агент вернул Response, не соответствующий схеме.",
                recovery=recovery,
                evidence=[raw],
                details={"validation_errors": error.errors()},
            )
            runner_feedback_ids.append(reported["id"])
            messages = list(final_state["messages"])
            messages.append(HumanMessage(content=validation_error_response(
                error,
                recovery=recovery,
            )))
            continue
        problem = check_response(response, raw)
        if problem:
            reported = feedback.runner_problem(
                agent="parser",
                category=problem["error"],
                message=problem["message"],
                recovery=problem["recovery"],
                evidence=[raw],
                details=problem["details"],
            )
            runner_feedback_ids.append(reported["id"])
            messages = list(final_state["messages"])
            messages.append(HumanMessage(content=error_response(
                problem["error"],
                problem["message"],
                recovery=problem["recovery"],
                details=problem["details"],
            )))
            continue
        feedback.resolve_recovered(
            runner_feedback_ids,
            "Финальный Response прошёл независимую проверку схемы, RAW и сохранения.",
        )
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
    try:
        response = run_step(
            agent,
            [HumanMessage(content=RESEARCH_PROMPT.format(log=raw))],
            raw,
            show_response=show_response,
            show_tools=show_tools,
            compact_tools=compact_tools,
        )
        saved = storage.save_research(response.model_dump(mode="json"))
    except Exception as error:
        feedback.runner_problem(
            agent="parser",
            category="research_failed",
            message=f"Исследование parser-агента завершилось ошибкой: {type(error).__name__}: {error}",
            evidence=[raw],
            details={"exception_type": type(error).__name__},
        )
        raise
    print(f"Research result saved: id={saved['id']}")
    return response, saved["id"]


def run_batch_step(
    agent,
    history: list[BaseMessage],
    batch: ResearchBatch,
    show_response: bool = True,
    show_tools: bool = True,
    compact_tools: bool = False,
) -> ResearchBatchResponse:
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
            response = ResearchBatchResponse.model_validate_json(content or "")
        except ValidationError as error:
            messages = list(final_state["messages"])
            messages.append(HumanMessage(content=validation_error_response(
                error,
                recovery=(
                    "Не повторяй исследование и не вызывай инструменты. Исправь только финальный "
                    "ResearchBatchResponse и верни полный JSON-объект без markdown и пояснений."
                ),
            )))
            continue
        problem = check_batch_response(response, batch)
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


def run_research_batch(
    agent,
    batch: ResearchBatch,
    show_response: bool = True,
    show_tools: bool = True,
    compact_tools: bool = False,
) -> tuple[ResearchBatchResponse, list[int]]:
    response = run_batch_step(
        agent,
        [HumanMessage(content=RESEARCH_BATCH_PROMPT.format(batch=batch.model_dump_json()))],
        batch,
        show_response=show_response,
        show_tools=show_tools,
        compact_tools=compact_tools,
    )
    research_ids = []
    for group in response.groups:
        saved = storage.save_research(group.response.model_dump(mode="json"))
        research_ids.append(saved["id"])
        print(f"Research result saved: id={saved['id']} samples={group.sample_ids}")
    return response, research_ids


def make_agent(target: str = "research"):
    target_prompt = MP_TEXT_TARGET_PROMPT if target == "mp_text" else ""
    return create_agent(
        make_model(),
        tools=TOOLS + (MP_TEXT_TOOLS if target == "mp_text" else []),
        middleware=[handle_tool_errors],
        system_prompt=SYSTEM_PROMPT + target_prompt + json.dumps(Response.model_json_schema(), ensure_ascii=False),
    )


def make_batch_agent(target: str = "research"):
    target_prompt = MP_TEXT_TARGET_PROMPT if target == "mp_text" else ""
    return create_agent(
        make_model(),
        tools=TOOLS + (MP_TEXT_TOOLS if target == "mp_text" else []),
        middleware=[handle_tool_errors],
        system_prompt=RESEARCH_BATCH_SYSTEM_PROMPT + target_prompt + json.dumps(
            ResearchBatchResponse.model_json_schema(),
            ensure_ascii=False,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Исследование одной RAW-строки лога.")
    parser.add_argument("raw", nargs="?", help="RAW-строка; без аргумента запускается диалог.")
    parser.add_argument("--target", choices=["research", "mp_text"], default="research")
    args = parser.parse_args()

    agent = make_agent(args.target)
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
