import argparse
import json
from pathlib import Path

from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from agent import make_model
import feedback
import normalization
from prompts import NORMALIZATION_PROMPT, NORMALIZATION_SYSTEM_PROMPT
from schemas import NormalizationRevision
import storage
from tools import NORMALIZATION_TOOLS, error_response, handle_tool_errors, validation_error_response


def make_normalization_agent():
    return create_agent(
        make_model(),
        tools=NORMALIZATION_TOOLS,
        middleware=[handle_tool_errors],
        system_prompt=NORMALIZATION_SYSTEM_PROMPT + json.dumps(
            NormalizationRevision.model_json_schema(), ensure_ascii=False
        ),
    )


def check_revision(response: NormalizationRevision) -> dict | None:
    saved = storage.get_normalization(
        response.family_id,
        response.revision,
        response.parser_revision,
    )
    if not saved["ok"]:
        return {
            "error": "normalization_not_persisted",
            "message": "Normalization revision не найдена в SQLite.",
            "recovery": "Вызови save_normalization после успешного evaluate и затем get_normalization.",
            "details": saved,
        }
    stored = saved["normalization"]
    expected = response.model_dump(mode="json")
    actual = {key: stored[key] for key in expected}
    if actual != expected:
        return {
            "error": "normalization_not_persisted",
            "message": "Финальный Response не совпадает с сохранённой normalization revision.",
            "recovery": "Верни точные сохранённые fields/evidence/description без изменений.",
            "details": {"expected": expected, "actual": actual},
        }
    evaluation = normalization.evaluate_normalization(
        response.family_id,
        response.parser_revision,
        response.fields,
        response.evidence,
    )
    if not evaluation["ok"]:
        return {
            "error": "normalization_validation_failed",
            "message": "Сохранённая normalization revision не прошла независимую проверку evidence.",
            "recovery": "Исправь operations или descriptions и повтори evaluate/save с новой revision.",
            "details": evaluation,
        }
    return None


def run_normalization(
    agent,
    family_id: str,
    parser_revision: int,
    source_default_year: int | None = None,
    additional_evidence: list[str] | None = None,
) -> tuple[NormalizationRevision, int]:
    try:
        return _run_normalization(
            agent,
            family_id,
            parser_revision,
            source_default_year,
            additional_evidence,
        )
    except Exception as error:
        feedback.runner_problem(
            agent="normalization",
            category="normalization_agent_failed",
            message=f"Normalization-агент завершился ошибкой: {type(error).__name__}: {error}",
            family_id=family_id,
            parser_revision=parser_revision,
            evidence=additional_evidence,
            details={"exception_type": type(error).__name__},
        )
        raise


def _run_normalization(
    agent,
    family_id: str,
    parser_revision: int,
    source_default_year: int | None = None,
    additional_evidence: list[str] | None = None,
) -> tuple[NormalizationRevision, int]:
    runner_feedback_ids = []
    messages = [HumanMessage(content=NORMALIZATION_PROMPT.format(
        family_id=family_id,
        parser_revision=parser_revision,
        source_default_year=source_default_year or "не задан",
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
            response = NormalizationRevision.model_validate_json(content or "")
        except ValidationError as error:
            recovery = "Исправь только финальный NormalizationRevision и верни полный JSON без markdown."
            reported = feedback.runner_problem(
                agent="normalization",
                category="invalid_response",
                message="Normalization-агент вернул Response, не соответствующий схеме.",
                recovery=recovery,
                family_id=family_id,
                parser_revision=parser_revision,
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
                agent="normalization",
                category=problem["error"],
                message=problem["message"],
                recovery=problem["recovery"],
                family_id=family_id,
                parser_revision=parser_revision,
                normalization_revision=response.revision,
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
            "Финальная normalization revision прошла независимую проверку схемы, evidence и сохранения.",
        )
        saved = storage.save_research({
            "type": "normalization",
            "normalization": response.model_dump(mode="json"),
        })
        print(f"Normalization research saved: id={saved['id']}", flush=True)
        return response, saved["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Нормализация полей существующей parser Family.")
    parser.add_argument("family_id")
    parser.add_argument("parser_revision", type=int)
    parser.add_argument("--source-default-year", type=int, help="Внешний год для timestamp без года, например 2023.")
    parser.add_argument("--evidence-file", type=Path, help="Дополнительные RAW evidence, по одной строке на запись.")
    args = parser.parse_args()
    additional_evidence = (
        [line for line in args.evidence_file.read_text(encoding="utf-8").splitlines() if line]
        if args.evidence_file else None
    )
    run_normalization(
        make_normalization_agent(),
        args.family_id,
        args.parser_revision,
        args.source_default_year,
        additional_evidence,
    )


if __name__ == "__main__":
    main()
