import grok
import pipeline
from schemas import FieldConstraint, GrokPattern, ParserResult, Response
import storage


def check_response(response: Response, raw: str) -> dict | None:
    structure_paths = {layer.path for layer in response.structure}
    invalid_sources = [
        {"field": field.name, "source": field.source}
        for field in response.record_schema
        if field.category == "dynamic" and field.source not in structure_paths
    ]
    if invalid_sources:
        return {
            "error": "contract_source_mismatch",
            "message": "Каждый dynamic record_schema.source должен быть точным path из structure.",
            "recovery": (
                "Исправь structure или record_schema.source: добавь отсутствующие пути значений "
                "и используй канонический путь $ для raw. Затем повтори полный Response."
            ),
            "details": {"invalid_sources": invalid_sources},
        }

    if response.parser.kind == "pipeline":
        return check_pipeline_response(response, raw)

    pattern_issues = grok.validate_patterns(response.parser.patterns)
    if pattern_issues:
        return {
            "error": "grok_pattern_validation_failed",
            "message": "Grok-конструкции не прошли зависимости или исполняемые примеры.",
            "recovery": (
                "Исправь определения и positive/negative examples. Примеры должны быть полными "
                "входами для соответствующей конструкции, затем повтори execute_grok_parser."
            ),
            "details": {"issues": pattern_issues},
        }

    registry_conflicts = []
    for pattern in response.parser.patterns:
        existing = storage.get_grok(pattern.name)
        if not existing["ok"]:
            continue
        stored = existing["pattern"]
        if stored["definition"] != pattern.definition:
            registry_conflicts.append(pattern.name)
    if registry_conflicts:
        return {
            "error": "grok_registry_conflict",
            "message": "Response patterns расходятся с глобальным Grok Registry.",
            "recovery": "Используй сохранённые definition/description/examples или сохрани согласованную конструкцию под новым именем.",
            "details": {"patterns": registry_conflicts},
        }

    patterns = {pattern.name: pattern.definition for pattern in response.parser.patterns}
    parser_result = grok.execute_parser(response.parser.layers, patterns, raw)
    validation = grok.validate_result(
        ParserResult.model_validate(parser_result),
        response.record_schema,
        response.parser.constraints,
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

    pattern_objects = {
        pattern.name: pattern.model_dump(mode="json") for pattern in response.parser.patterns
    }
    layers = [layer.model_dump(mode="json") for layer in response.parser.layers]
    record_schema = [field.model_dump(mode="json") for field in response.record_schema]
    constraints = [constraint.model_dump(mode="json") for constraint in response.parser.constraints]
    for item in storage.list_families()["families"]:
        family = storage.get_family(item["family_id"])["family"]
        family_patterns = {
            pattern["name"]: GrokPattern.model_validate(pattern).model_dump(mode="json")
            for pattern in family["patterns"]
        }
        if (
            family["layers"] == layers
            and family_patterns == pattern_objects
            and family["record_schema"] == record_schema
            and [
                FieldConstraint.model_validate(rule).model_dump(mode="json")
                for rule in family["constraints"]
            ] == constraints
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


def check_pipeline_response(response: Response, raw: str) -> dict | None:
    parser_result = pipeline.execute_pipeline(response.parser.stages, raw)
    validation = grok.validate_result(
        ParserResult.model_validate(parser_result),
        response.record_schema,
        response.parser.constraints,
    )
    if not validation["ok"]:
        return {
            "error": "parser_validation_failed",
            "message": "Финальный pipeline не прошёл независимое исполнение на исходной RAW-строке.",
            "recovery": (
                "Исправь stages/record_schema, вызови execute_pipeline_parser, "
                "validate_pipeline_result и evaluate_pipeline_candidate, затем сохрани корректную Family."
            ),
            "details": {
                "issues": validation["issues"],
                "failed_layer": parser_result.get("failed_layer"),
                "residual": parser_result.get("residual"),
            },
        }

    stages = [stage.model_dump(mode="json") for stage in response.parser.stages]
    record_schema = [field.model_dump(mode="json") for field in response.record_schema]
    constraints = [constraint.model_dump(mode="json") for constraint in response.parser.constraints]
    for item in storage.list_families()["families"]:
        family = storage.get_family(item["family_id"])["family"]
        if (
            family["stages"] == stages
            and family["patterns"] == []
            and family["layers"] == []
            and family["record_schema"] == record_schema
            and [
                FieldConstraint.model_validate(rule).model_dump(mode="json")
                for rule in family["constraints"]
            ] == constraints
        ):
            return None
    return {
        "error": "parser_not_persisted",
        "message": "Исполненный Response.parser не совпадает ни с одной сохранённой pipeline Family.",
        "recovery": (
            "Для новой или изменённой грамматики сохрани pipeline через save_pipeline_family "
            "и проверь через get_parser_family. Для reuse верни существующие stages без изменений."
        ),
        "details": {"expected": "stages, record_schema и constraints должны совпасть с содержимым SQLite."},
    }
