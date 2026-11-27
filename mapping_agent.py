import argparse
import json
from pathlib import Path

from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from agent import make_model
import feedback
import mapping
from prompts import MAPPING_PROMPT, MAPPING_SYSTEM_PROMPT, MP_TEXT_MAPPING_TARGET_PROMPT
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
    additional_evidence: list[str] | None = None,
) -> tuple[MappingRevision, int]:
    try:
        return _run_mapping(
            agent,
            family_id,
            parser_revision,
            normalization_revision,
            source_timezone,
            additional_evidence,
        )
    except Exception as error:
        feedback.runner_problem(
            agent="mapping",
            category="mapping_agent_failed",
            message=f"Mapping-агент завершился ошибкой: {type(error).__name__}: {error}",
            family_id=family_id,
            parser_revision=parser_revision,
            normalization_revision=normalization_revision,
            evidence=additional_evidence,
            details={"exception_type": type(error).__name__},
        )
        raise


def _run_mapping(
    agent,
    family_id: str,
    parser_revision: int,
    normalization_revision: int,
    source_timezone: str | None = None,
    additional_evidence: list[str] | None = None,
) -> tuple[MappingRevision, int]:
    runner_feedback_ids = []
    family = storage.get_family(family_id, parser_revision)
    target_prompt = (
        MP_TEXT_MAPPING_TARGET_PROMPT
        if family.get("ok") and family["family"].get("target") == "mp_text" else ""
    )
    messages = [HumanMessage(content=target_prompt + MAPPING_PROMPT.format(
        family_id=family_id,
        parser_revision=parser_revision,
        normalization_revision=normalization_revision,
        source_timezone=source_timezone or "не задана",
        additional_evidence="\n".join(additional_evidence or []) or "нет",
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
                    try:
                        failed = json.loads(message.content).get("ok") is False
                    except (TypeError, ValueError, AttributeError):
                        failed = False
                    if failed:
                        print(f"Tool error: {message.content}", flush=True)
                    else:
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
            recovery = "Исправь только финальный MappingRevision и верни полный JSON без markdown."
            reported = feedback.runner_problem(
                agent="mapping",
                category="invalid_response",
                message="Mapping-агент вернул Response, не соответствующий схеме.",
                recovery=recovery,
                family_id=family_id,
                parser_revision=parser_revision,
                normalization_revision=normalization_revision,
                evidence=additional_evidence,
                details={"validation_errors": error.errors()},
            )
            runner_feedback_ids.append(reported["id"])
            messages = list(final_state["messages"])
            messages.append(HumanMessage(content=validation_error_response(
                error,
                recovery=recovery,
            )))
            continue
        problem = check_revision(response)
        if problem:
            reported = feedback.runner_problem(
                agent="mapping",
                category=problem["error"],
                message=problem["message"],
                recovery=problem["recovery"],
                family_id=family_id,
                parser_revision=parser_revision,
                normalization_revision=normalization_revision,
                mapping_revision=response.revision,
                evidence=response.evidence,
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
            "Финальная mapping revision прошла независимую проверку схемы, evidence и сохранения.",
        )
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
    parser.add_argument("--evidence-file", type=Path, help="Дополнительные RAW evidence, по одной строке на запись.")
    args = parser.parse_args()
    additional_evidence = (
        [line for line in args.evidence_file.read_text(encoding="utf-8").splitlines() if line]
        if args.evidence_file else None
    )
    run_mapping(
        make_mapping_agent(),
        args.family_id,
        args.parser_revision,
        args.normalization_revision,
        args.source_timezone,
        additional_evidence,
    )


if __name__ == "__main__":
    main()
