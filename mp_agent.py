import argparse
import json
from pathlib import Path

from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from agent import make_model
import feedback
from log_batching import prepare_log_batches
import mp_native
from mp_prompts import MP_TEXT_PROMPT, MP_TEXT_SYSTEM_PROMPT
from mp_schemas import MpTextCandidateResponse
from mp_tools import make_mp_text_tools
import storage
import taxonomy
from tools import error_response, handle_tool_errors, validation_error_response


def ensure_taxonomy_loaded() -> None:
    if storage.list_taxonomy_fields(taxonomy.TAXONOMY):
        return
    reference = taxonomy.parse_reference()
    storage.replace_taxonomy(
        taxonomy.TAXONOMY,
        reference["fields"],
        reference["values"],
        reference["categories"],
    )


def make_mp_text_agent(output_root: Path, mpsiem: Path, native_mode: str):
    return create_agent(
        make_model(),
        tools=make_mp_text_tools(output_root, mpsiem, native_mode),
        middleware=[handle_tool_errors],
        system_prompt=MP_TEXT_SYSTEM_PROMPT + json.dumps(
            MpTextCandidateResponse.model_json_schema(), ensure_ascii=False
        ),
    )


def corpus_prompt(raw_logs: list[str]) -> str:
    batches = prepare_log_batches(raw_logs, samples_per_cluster=6)
    return json.dumps({
        "total_raw": len(raw_logs),
        "clusters": [batch.model_dump(mode="json") for batch in batches],
        "coverage_contract": "Runner исполнит весь corpus после export, а не только показанные samples.",
    }, ensure_ascii=False)


def check_export(response: MpTextCandidateResponse) -> dict | None:
    saved = storage.get_mp_export(response.source, response.revision)
    if not saved["ok"]:
        return {
            "error": "mp_export_not_persisted",
            "message": "Экспорт MP candidate не найден в Registry.",
            "recovery": "Вызови export_mp_normalizer после evaluate с native ok=true.",
            "details": saved,
        }
    exported = saved["export"]
    for key in ("yaml_path", "conf_path", "fixtures_path", "report_path"):
        if getattr(response, key) != exported[key] or not Path(exported[key]).is_file():
            return {
                "error": "mp_export_response_mismatch",
                "message": f"Response.{key} не совпадает с сохранённым доступным файлом.",
                "recovery": "Верни фактические данные последнего export.",
                "details": exported,
            }
    for key in ("rules", "positive_fixtures", "negative_fixtures"):
        if getattr(response, key) != exported[key]:
            return {
                "error": "mp_export_response_mismatch",
                "message": f"Response.{key} не совпадает с Registry.",
                "recovery": "Верни фактические данные последнего export.",
                "details": exported,
            }
    if not response.native_ok or not exported["native_ok"]:
        return {
            "error": "mp_export_not_native_validated",
            "message": "Нельзя завершить без native ok=true.",
            "recovery": "Исправь candidate по native report и повтори evaluate/export.",
            "details": exported,
        }
    blocking = [
        item for item in storage.list_feedback(status="open", limit=200)["feedback"]
        if item["agent"] == "mp_text"
        and item["blocking"]
        and item["family_id"] in {None, response.source}
    ]
    if blocking:
        return {
            "error": "mp_export_has_blocking_feedback",
            "message": f"Есть {len(blocking)} открытых blocking feedback; пакет не готов.",
            "recovery": (
                "Устрани проблемы и закрой feedback через resolve_mp_feedback. "
                "accepted_uncertainty допустима только для внешней неопределённости, не для дефекта правил."
            ),
            "details": {"feedback": blocking},
        }
    return None


def validate_full_corpus(
    response: MpTextCandidateResponse,
    raw_logs: list[str],
    mpsiem: Path,
    native_mode: str,
) -> dict:
    fixtures = {
        "positive": [
            {"name": f"corpus:{index}", "event": {"message": raw, "mime": "TEXT"}, "expected": {}}
            for index, raw in enumerate(raw_logs)
        ],
        "negative": [],
    }
    report = mp_native.validate_rules_from_text(
        Path(response.yaml_path).read_text(encoding="utf-8"),
        fixtures,
        mpsiem=mpsiem,
        mode=native_mode,
    )
    report_path = Path(response.report_path)
    package = json.loads(report_path.read_text(encoding="utf-8"))
    package["corpus"] = {
        "ok": bool(report.get("ok")),
        "total": len(raw_logs),
        "report": report,
    }
    package["ok"] = bool(package.get("ok") and report.get("ok"))
    report_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def run_mp_text(
    agent,
    source: str,
    raw_logs: list[str],
    mpsiem: Path,
    native_mode: str,
    source_timezone: str | None = None,
) -> MpTextCandidateResponse:
    ensure_taxonomy_loaded()
    messages = [HumanMessage(content=MP_TEXT_PROMPT.format(
        source=source,
        source_timezone=source_timezone or "не задана",
        corpus=corpus_prompt(raw_logs),
    ))]
    runner_feedback_ids = []
    while True:
        final_state = None
        for chunk in agent.stream({"messages": messages}, stream_mode=["updates", "values"], version="v2"):
            if chunk["type"] == "values":
                final_state = chunk["data"]
                continue
            for component, update in chunk["data"].items():
                if component not in ("model", "tools"):
                    continue
                message = update["messages"][-1]
                if isinstance(message, AIMessage):
                    for call in message.tool_calls:
                        print(f"Tool: {call['name']}", flush=True)
                elif isinstance(message, ToolMessage):
                    print(f"Tool status: {message.status}", flush=True)
        if final_state is None:
            raise RuntimeError("MP TEXT агент не вернул финальное состояние")
        content = next(
            (
                message.content for message in reversed(final_state["messages"])
                if isinstance(message, AIMessage) and message.content
            ),
            None,
        )
        try:
            response = MpTextCandidateResponse.model_validate_json(content or "")
        except ValidationError as error:
            recovery = "Исправь только финальный MpTextCandidateResponse и верни JSON без markdown."
            reported = feedback.runner_problem(
                agent="mp_text",
                category="invalid_response",
                message="MP TEXT агент вернул Response не по схеме.",
                recovery=recovery,
                evidence=raw_logs[:5],
                details={"validation_errors": error.errors()},
            )
            runner_feedback_ids.append(reported["id"])
            messages = list(final_state["messages"])
            messages.append(HumanMessage(content=validation_error_response(error, recovery)))
            continue
        problem = check_export(response)
        if problem is None and response.source == source:
            corpus_report = validate_full_corpus(response, raw_logs, mpsiem, native_mode)
            if corpus_report.get("ok"):
                feedback.resolve_recovered(
                    runner_feedback_ids,
                    "MP export прошёл strict fixtures и полный native corpus.",
                )
                print(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, indent=2))
                return response
            failures = [
                result for result in corpus_report.get("results", []) if not result.get("ok")
            ]
            problem = {
                "error": "full_corpus_validation_failed",
                "message": f"Полный corpus не покрыт: {len(failures)} ошибок.",
                "recovery": (
                    "Исправь candidate для показанных RAW, увеличь revision, повтори evaluate/export. "
                    "Не удаляй прежние positive/negative fixtures."
                ),
                "details": {"native": corpus_report, "failures": failures[:20]},
            }
        elif problem is None:
            problem = {
                "error": "mp_export_wrong_source",
                "message": f"Response source={response.source}, ожидался {source}.",
                "recovery": "Экспортируй candidate для исходного source.",
                "details": {},
            }
        reported = feedback.runner_problem(
            agent="mp_text",
            category=problem["error"],
            message=problem["message"],
            recovery=problem["recovery"],
            evidence=raw_logs[:5],
            details=problem["details"],
        )
        runner_feedback_ids.append(reported["id"])
        messages = list(final_state["messages"])
        messages.append(HumanMessage(content=error_response(
            problem["error"], problem["message"], recovery=problem["recovery"], details=problem["details"]
        )))


def main() -> None:
    parser = argparse.ArgumentParser(description="Неизвестный RAW corpus → проверенный mp_normalizer YAML + CONF.")
    parser.add_argument("source")
    parser.add_argument("file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("generated"))
    parser.add_argument("--mpsiem", type=Path, default=mp_native.DEFAULT_MPSIEM)
    parser.add_argument("--native-mode", choices=["docker", "local"], default="docker")
    parser.add_argument("--source-timezone")
    args = parser.parse_args()
    raw_logs = [line for line in args.file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not raw_logs:
        parser.error("Пустой corpus")
    agent = make_mp_text_agent(args.output, args.mpsiem, args.native_mode)
    run_mp_text(agent, args.source, raw_logs, args.mpsiem, args.native_mode, args.source_timezone)


if __name__ == "__main__":
    main()
