import argparse
import json

from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from agent import make_model
import mapping
from prompts import MAPPING_PROMPT, MAPPING_SYSTEM_PROMPT
from schemas import MappingRevision
import storage
from tools import MAPPING_TOOLS, error_response, handle_tool_errors, validation_error_response


def make_mapping_agent():
    return create_agent(
        make_model(),
        tools=MAPPING_TOOLS,
        middleware=[handle_tool_errors],
        system_prompt=MAPPING_SYSTEM_PROMPT + json.dumps(
            MappingRevision.model_json_schema(), ensure_ascii=False
        ),
    )


def check_revision(response: MappingRevision) -> dict | None:
    saved = storage.get_mapping(
        response.family_id,
        response.revision,
        response.normalization_revision,
    )
    if not saved["ok"]:
        return {
            "error": "mapping_not_persisted",
            "message": "Mapping revision не найдена в SQLite.",
            "recovery": "Вызови save_mapping после успешного evaluate и затем get_mapping.",
            "details": saved,
        }
    stored = saved["mapping"]
    expected = response.model_dump(mode="json")
    actual = {key: stored[key] for key in expected}
    if actual != expected:
        return {
            "error": "mapping_not_persisted",
            "message": "Финальный Response не совпадает с сохранённой mapping revision.",
            "recovery": "Верни точные сохранённые mappings/evidence/description без изменений.",
            "details": {"expected": expected, "actual": actual},
        }
    evaluation = mapping.evaluate_mappings(
        response.family_id,
        response.parser_revision,
        response.normalization_revision,
        response.mappings,
        response.evidence,
        response.source_timezone,
    )
    if not evaluation["ok"]:
        return {
            "error": "mapping_validation_failed",
            "message": "Сохранённая mapping revision не прошла независимую проверку evidence.",
            "recovery": "Исправь mappings по справочнику и повтори evaluate/save с новой revision.",
            "details": evaluation,
        }
    return None


def run_mapping(
    agent,
    family_id: str,
    parser_revision: int,
    normalization_revision: int,
    source_timezone: str | None = None,
) -> tuple[MappingRevision, int]:
    messages = [HumanMessage(content=MAPPING_PROMPT.format(
        family_id=family_id,
        parser_revision=parser_revision,
        normalization_revision=normalization_revision,
        source_timezone=source_timezone or "не задана",
    ))]
    while True:
        final_state = None
        for chunk in agent.stream({"messages": messages}, stream_mode=["updates", "values"], version="v2"):
            if chunk["type"] == "values":
                final_state = chunk["data"]
                continue
            for source, update in chunk["data"].items():
                if source not in ("model", "tools"):
                    continue
                message = update["messages"][-1]
                if isinstance(message, AIMessage):
                    for call in message.tool_calls:
                        print(f"Tool: {call['name']}", flush=True)
                elif isinstance(message, ToolMessage):
                    print(f"Tool status: {message.status}", flush=True)
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
            response = MappingRevision.model_validate_json(content or "")
        except ValidationError as error:
            messages = list(final_state["messages"])
            messages.append(HumanMessage(content=validation_error_response(
                error,
                recovery="Исправь только финальный MappingRevision и верни полный JSON без markdown.",
            )))
            continue
        problem = check_revision(response)
        if problem:
            messages = list(final_state["messages"])
            messages.append(HumanMessage(content=error_response(
                problem["error"],
                problem["message"],
                recovery=problem["recovery"],
                details=problem["details"],
            )))
            continue
        saved = storage.save_research({
            "type": "mapping",
            "mapping": response.model_dump(mode="json"),
        })
        print(f"Mapping research saved: id={saved['id']}", flush=True)
        return response, saved["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Семантический маппинг normalized Family в MP SIEM 8.0.")
    parser.add_argument("family_id")
    parser.add_argument("parser_revision", type=int)
    parser.add_argument("normalization_revision", type=int)
    parser.add_argument("--source-timezone", help="Внешняя timezone для naive timestamp, например UTC или Europe/Moscow.")
    args = parser.parse_args()
    run_mapping(
        make_mapping_agent(),
        args.family_id,
        args.parser_revision,
        args.normalization_revision,
        args.source_timezone,
    )


if __name__ == "__main__":
    main()
