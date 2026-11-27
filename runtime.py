import grok
import mp_text
import pipeline
from schemas import GrokLayer, PipelineStage


def execute_family(family: dict, raw: str) -> dict:
    if family.get("target") == "mp_text":
        return mp_text.execute_pattern(family["text_pattern"], raw)
    if family["stages"]:
        stages = [PipelineStage.model_validate(stage) for stage in family["stages"]]
        return pipeline.execute_pipeline(stages, raw)
    layers = [GrokLayer.model_validate(layer) for layer in family["layers"]]
    patterns = {pattern["name"]: pattern["definition"] for pattern in family["patterns"]}
    return grok.execute_parser(layers, patterns, raw)
