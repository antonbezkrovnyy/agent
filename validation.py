import grok
from schemas import ParserResult, Response
import storage


def check_response(response: Response, raw: str) -> dict | None:
    patterns = {pattern.name: pattern.definition for pattern in response.parser.patterns}
    parser_result = grok.execute_parser(response.parser.layers, patterns, raw)
    validation = grok.validate_result(
        ParserResult.model_validate(parser_result),
        response.record_schema,
    )
    if not validation["ok"]:
        return {
            "error": "parser_validation_failed",
            "message": "Финальный parser не прошёл независимое исполнение на исходной RAW-строке.",
            "recovery": (
                "Исправь parser, вызови execute_grok_parser, validate_grok_result и "
                "evaluate_candidate, затем сохрани корректную Family. Не выдумывай результат проверки."
            ),
            "details": {
                "issues": validation["issues"],
                "failed_layer": parser_result.get("failed_layer"),
                "residual": parser_result.get("residual"),
            },
        }

    layers = [layer.model_dump(mode="json") for layer in response.parser.layers]
    record_schema = [field.model_dump(mode="json") for field in response.record_schema]
    for item in storage.list_families()["families"]:
        family = storage.get_family(item["family_id"])["family"]
        family_patterns = {
            pattern["name"]: pattern["definition"] for pattern in family["patterns"]
        }
        if (
            family["layers"] == layers
            and family_patterns == patterns
            and family["record_schema"] == record_schema
        ):
            return None
    return {
        "error": "parser_not_persisted",
        "message": "Исполненный Response.parser не совпадает ни с одной сохранённой Family.",
        "recovery": (
            "Для новой или изменённой грамматики сохрани все конструкции и Family через "
            "save_grok_pattern/save_parser_family и проверь через get_parser_family. "
            "Для reuse верни parser и record_schema существующей Family без изменений."
        ),
        "details": {"expected": "patterns, layers и record_schema должны совпасть с содержимым SQLite."},
    }
