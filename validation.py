import grok
import mp_text
import pipeline
from schemas import (
    FieldConstraint,
    GrokPattern,
    Observation,
    ParserResult,
    ResearchBatch,
    ResearchBatchResponse,
    Response,
)
import storage


def check_response(response: Response, raw: str | list[str]) -> dict | None:
    raws = [raw] if isinstance(raw, str) else raw
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
        return check_pipeline_response(response, raws)
    if response.parser.kind == "mp_text":
        return check_mp_text_response(response, raws)

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
    for sample_index, sample_raw in enumerate(raws):
        parser_result = grok.execute_parser(response.parser.layers, patterns, sample_raw)
        validation = grok.validate_result(
            ParserResult.model_validate(parser_result),
            response.record_schema,
            response.parser.constraints,
        )
        if not validation["ok"]:
            return {
                "error": "parser_validation_failed",
                "message": "Финальный parser не прошёл независимое исполнение на исходных RAW-строках.",
                "recovery": (
                    "Исправь parser, вызови execute_grok_parser, validate_grok_result и "
                    "evaluate_candidate, затем сохрани корректную Family. Не выдумывай результат проверки."
                ),
                "details": {
                    "sample_index": sample_index,
                    "raw": sample_raw,
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


def check_mp_text_response(response: Response, raws: list[str]) -> dict | None:
    evaluation = mp_text.evaluate_observations(
        response.parser.text_pattern,
        response.record_schema,
        [Observation(raw=raw) for raw in raws],
        response.parser.constraints,
        response.parser.negative_evidence,
    )
    if not evaluation["ok"]:
        return {
            "error": "mp_text_validation_failed",
            "message": "Финальный MP TEXT parser не прошёл native captures, schema или negative evidence.",
            "recovery": (
                "Исправь text_pattern, вызови execute/evaluate_mp_text_parser и сохрани Family "
                "через save_mp_text_family. Не выдумывай native результат."
            ),
            "details": evaluation,
        }
    record_schema = [field.model_dump(mode="json") for field in response.record_schema]
    constraints = [
        FieldConstraint.model_validate(rule).model_dump(mode="json")
        for rule in response.parser.constraints
    ]
    for item in storage.list_families()["families"]:
        family = storage.get_family(item["family_id"])["family"]
        if (
            family.get("target") == "mp_text"
            and family.get("text_pattern") == response.parser.text_pattern
            and family.get("negative_evidence", []) == response.parser.negative_evidence
            and family["record_schema"] == record_schema
            and family["constraints"] == constraints
        ):
            return None
    return {
        "error": "parser_not_persisted",
        "message": "MP TEXT Response не совпадает ни с одной сохранённой Family target=mp_text.",
        "recovery": "Сохрани через save_mp_text_family и проверь get_parser_family; для reuse верни существующую revision без изменений.",
        "details": {"expected": "text_pattern, record_schema, negative_evidence и constraints должны совпасть с SQLite."},
    }


def check_pipeline_response(response: Response, raw: str | list[str]) -> dict | None:
    raws = [raw] if isinstance(raw, str) else raw
    for sample_index, sample_raw in enumerate(raws):
        parser_result = pipeline.execute_pipeline(response.parser.stages, sample_raw)
        validation = grok.validate_result(
            ParserResult.model_validate(parser_result),
            response.record_schema,
            response.parser.constraints,
        )
        if not validation["ok"]:
            return {
                "error": "parser_validation_failed",
                "message": "Финальный pipeline не прошёл независимое исполнение на исходных RAW-строках.",
                "recovery": (
                    "Исправь stages/record_schema, вызови execute_pipeline_parser, "
                    "validate_pipeline_result и evaluate_pipeline_candidate, затем сохрани корректную Family."
                ),
                "details": {
                    "sample_index": sample_index,
                    "raw": sample_raw,
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


def check_batch_response(response: ResearchBatchResponse, batch: ResearchBatch) -> dict | None:
    samples_by_id = {sample.sample_id: sample.raw for sample in batch.samples}
    expected_ids = set(samples_by_id)
    assigned_ids = [
        sample_id
        for group in response.groups
        for sample_id in group.sample_ids
    ] + response.unresolved_sample_ids
    assigned_set = set(assigned_ids)
    duplicate_ids = sorted({sample_id for sample_id in assigned_ids if assigned_ids.count(sample_id) > 1})
    unknown_ids = sorted(assigned_set - expected_ids)
    missing_ids = sorted(expected_ids - assigned_set)
    if duplicate_ids or unknown_ids or missing_ids:
        return {
            "error": "batch_assignment_invalid",
            "message": "Каждый sample_id должен ровно один раз попасть в группу или unresolved_sample_ids.",
            "recovery": (
                "Исправь только распределение sample_ids. Не дублируй примеры, не придумывай "
                "новые sample_id и не оставляй входные примеры без назначения."
            ),
            "details": {
                "duplicate_sample_ids": duplicate_ids,
                "unknown_sample_ids": unknown_ids,
                "missing_sample_ids": missing_ids,
            },
        }

    for group_index, group in enumerate(response.groups):
        problem = check_response(
            group.response,
            [samples_by_id[sample_id] for sample_id in group.sample_ids],
        )
        if problem:
            return {
                **problem,
                "details": {
                    **problem.get("details", {}),
                    "group_index": group_index,
                    "sample_ids": group.sample_ids,
                },
            }
    return None
