"""Детерминированные проверки и независимая LLM-оценка завершённого run."""

import json

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from models import EvaluationIssue, EvaluationResult
from phoenix_client import compact_trace


SYSTEM_PROMPT = """Ты — независимый evaluator завершённого расследования LogiCore.
Оценивай фактический run по state и Phoenix trace, а не красоту текста. Ты не выполняешь
расследование и не исправляешь отчёт.

Проверяй:
- соответствие claims существующим Evidence и их результатам;
- слишком категоричные выводы при малом окне, отсутствии baseline или неполных данных;
- ошибки и partial инструментов, непокрытые limitations;
- корректность Intent, окон, quick/deep и обязательных этапов;
- tool limits, ошибки памяти и критиков, признаки пустого или преждевременного ответа.

Не требуй Evidence для обычного разговора и read-only обзора памяти. Не считай отсутствие
аномалии ошибкой. Не выдумывай проблемы и не считай trace доказательством истинности
внешних данных. В issues указывай конкретные Evidence/span ID там, где они доступны.
verdict=pass только при отсутствии существенных замечаний, warn при ограничениях,
fail при unsupported claims, критических runtime errors или несоответствии ответа задаче.
score — качество run от 0 до 1. Верни строго схему EvaluationResult."""


def _span_kind(span: dict) -> str:
    attributes = span.get("attributes") or {}
    if isinstance(attributes, str):
        try:
            attributes = json.loads(attributes)
        except json.JSONDecodeError:
            return ""
    if not isinstance(attributes, dict):
        return ""
    return str(attributes.get("openinference.span.kind") or "").upper()


def deterministic_checks(state: dict, trace: dict) -> tuple[list[EvaluationIssue], dict]:
    issues: list[EvaluationIssue] = []
    spans = trace.get("spans") or []
    failed_spans = [span for span in spans if span.get("statusCode") == "ERROR"]
    if failed_spans:
        issues.append(EvaluationIssue(
            severity="critical",
            category="runtime",
            summary=f"В Phoenix отмечены ошибочные spans: {len(failed_spans)}.",
            span_ids=[span.get("spanId") for span in failed_spans[:10]],
            recommendation="Проверить ошибки runtime и повторить затронутый этап.",
        ))
    evidence = state.get("evidence") or []
    evidence_ids = {item.get("id") for item in evidence}
    claims = (state.get("report_result") or {}).get("claims") or []
    missing = sorted({eid for claim in claims for eid in claim.get("evidence_ids") or [] if eid not in evidence_ids})
    if missing:
        issues.append(EvaluationIssue(
            severity="critical",
            category="unsupported_claim",
            summary=f"Claims ссылаются на отсутствующие Evidence: {', '.join(missing)}.",
            evidence_ids=missing,
            recommendation="Удалить ссылки или получить соответствующие Evidence.",
        ))
    incomplete = [item.get("id") for item in evidence if item.get("status") != "ok" or item.get("complete") is False]
    if incomplete:
        issues.append(EvaluationIssue(
            severity="warning",
            category="missing_data",
            summary=f"Неполные или ошибочные Evidence: {len(incomplete)}.",
            evidence_ids=incomplete[:10],
            recommendation="Проверить, что ограничения данных отражены в ответе.",
        ))
    if state.get("memory_error"):
        issues.append(EvaluationIssue(
            severity="warning",
            category="memory",
            summary=state["memory_error"],
            recommendation="Проверить доступность памяти; текущий run не считать автоматически невалидным.",
        ))
    critic = state.get("critic") or {}
    if critic.get("verdict") == "critic_unavailable":
        issues.append(EvaluationIssue(
            severity="warning",
            category="runtime",
            summary="Один из критиков был недоступен.",
            recommendation="Учитывать снижение покрытия проверки отчёта.",
        ))
    scope = state.get("investigation_scope") or {}
    plan_items = (state.get("investigation_plan") or {}).get("items") or []
    if scope.get("max_items") and len(plan_items) > scope["max_items"]:
        issues.append(EvaluationIssue(
            severity="critical",
            category="scope_creep",
            summary=f"План содержит {len(plan_items)} items при Scope max_items={scope['max_items']}.",
            recommendation="Сократить текущий этап; новые направления оставить в optional.",
        ))
    run_metrics = list(state.get("run_metrics") or [])
    trace_tokens = sum(int(span.get("tokenCountTotal") or 0) for span in spans)
    metrics = {
        "trace_spans": trace.get("numSpans") or len(spans),
        "trace_errors": len(failed_spans),
        "trace_latency_ms": trace.get("latencyMs"),
        "llm_latency_ms": sum(int(span.get("latencyMs") or 0) for span in spans if _span_kind(span) == "LLM"),
        "tool_latency_ms": sum(int(span.get("latencyMs") or 0) for span in spans if _span_kind(span) == "TOOL"),
        "trace_tokens_total": trace_tokens,
        "tokens_total": sum(int(item.get("total_tokens") or 0) for item in run_metrics) or trace_tokens,
        "evidence_count": len(evidence),
        "incomplete_evidence_count": len(incomplete),
        "claims_count": len(claims),
        "unsupported_claims_count": len(missing),
        "unsupported_claims_ratio": len(missing) / len(claims) if claims else 0.0,
        "work_mode": state.get("work_mode") or "",
        "scope_mode": scope.get("mode") or "",
        "scope_max_items": scope.get("max_items") or 0,
        "replan_count": max(0, int((state.get("investigation_plan") or {}).get("version") or 1) - 1),
        "tool_calls_total": sum(int(item.get("tool_calls") or 0) for item in run_metrics),
        "repeated_tool_calls": sum(int(item.get("repeated_tool_calls") or 0) for item in run_metrics),
        "agent_duration_ms": sum(int(item.get("duration_ms") or 0) for item in run_metrics),
        "item_metrics": [item for item in run_metrics if item.get("work_item_id")],
        "memory_operations_count": len(state.get("memory_operations") or []),
    }
    return issues, metrics


def _llm(settings) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model_evaluator or settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_token,
        temperature=0,
        timeout=settings.evaluator_timeout_seconds,
        max_retries=1,
    )


def _prompt_value(value, *, max_items: int = 50, max_string: int = 10000):
    if isinstance(value, dict):
        return {key: _prompt_value(item, max_items=max_items, max_string=max_string) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_prompt_value(item, max_items=max_items, max_string=max_string) for item in value[:max_items]]
        if len(value) > max_items:
            items.append(f"… ещё {len(value) - max_items} элементов; полные данные в state")
        return items
    if isinstance(value, str) and len(value) > max_string:
        return value[:max_string] + "…[truncated]"
    return value


def _prompt_evidence(state: dict) -> list[dict]:
    records = list(state.get("evidence") or [])
    referenced = []
    for key in ("inv_result", "report_result"):
        result = state.get(key) or {}
        referenced.extend(result.get("evidence_ids") or [])
        for claim in result.get("claims") or []:
            referenced.extend(claim.get("evidence_ids") or [])
    for item in [*(state.get("findings") or []), *(state.get("hypotheses") or [])]:
        referenced.extend(item.get("evidence_ids") or [])
    wanted = set(referenced)
    selected = [item for item in records if item.get("id") in wanted] if wanted else records[-20:]
    return [
        {**item, "result": _prompt_value(item.get("result") or {})}
        for item in selected
    ]


def evaluate(state: dict, trace: dict, settings) -> EvaluationResult:
    issues, metrics = deterministic_checks(state, trace)
    payload = {
        "state": {
            **{
                key: state.get(key)
                for key in (
                    "intent", "work_mode", "context", "inv_result", "report_result",
                    "findings", "hypotheses", "memory_operations",
                    "memory_error", "critic", "rework", "progress",
                )
            },
            "evidence": _prompt_evidence(state),
        },
        "trace": compact_trace(
            trace,
            attribute_limit=getattr(settings, "evaluator_trace_attribute_limit", 2500),
        ),
        "deterministic_issues": [item.model_dump(mode="json") for item in issues],
        "metrics": metrics,
    }
    model = _llm(settings)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]
    try:
        result = model.with_structured_output(EvaluationResult, method="function_calling").invoke(messages)
    except Exception:
        result = model.with_structured_output(EvaluationResult, method="json_mode").invoke(messages)
    merged = [*issues, *result.issues]
    verdict = result.verdict
    if any(item.severity == "critical" for item in merged):
        verdict = "fail"
    elif any(item.severity == "warning" for item in merged) and verdict == "pass":
        verdict = "warn"
    score = result.score
    if verdict == "fail":
        score = min(score, 0.49)
    elif verdict == "warn":
        score = min(score, 0.79)
    return EvaluationResult(verdict=verdict, score=score, summary=result.summary, issues=merged[:10])
