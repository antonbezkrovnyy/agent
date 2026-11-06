"""Read-only доступ к Phoenix GraphQL и запись аннотаций оценки."""

import json

import httpx

RUN_SUMMARY_IDENTIFIER = "run-v1"


def run_summary_annotations(state: dict) -> list[dict]:
    """Детерминированная классификация run из state для trace-аннотаций.

    Каждая аннотация — одно измерение: run_type и scope_mode несут label,
    plan_execution, claims_support и hypothesis_coverage — score 0..1,
    critic_review — label с итогом критиков. Для простого чата только
    run_type/scope_mode, метрики расследования не пишутся.
    """
    intent = state.get("intent") or {}
    scope = state.get("investigation_scope") or {}
    plan = state.get("investigation_plan") or {}
    items = plan.get("items") or []
    hypotheses = state.get("hypotheses") or []
    critic = state.get("critic") or {}

    intent_kind = intent.get("intent") or ""
    work_mode = state.get("work_mode") or intent.get("work_mode") or ""
    if intent_kind == "continuation":
        run_type = "continuation"
    elif work_mode == "deep":
        run_type = "deep"
    elif work_mode == "quick":
        run_type = "quick"
    else:
        run_type = "chat"

    annotations = [{
        "name": "run_type",
        "label": run_type,
        "score": None,
        "explanation": intent.get("rationale") or "",
    }]
    scope_mode = scope.get("mode") or "none"
    max_items = scope.get("max_items") or 0
    annotations.append({
        "name": "scope_mode",
        "label": scope_mode,
        "score": None,
        "explanation": f"max_items={max_items}" if max_items else "",
    })

    if items:
        done = sum(1 for item in items if item.get("status") == "done")
        replans = max(0, int(plan.get("version") or 1) - 1)
        creep = max_items and len(items) > max_items
        explanation = f"items {done}/{len(items)} done; replans={replans}"
        if creep:
            explanation += f"; scope creep: {len(items)} > max_items={max_items}"
        annotations.append({
            "name": "plan_execution",
            "label": None,
            "score": round(done / len(items), 4),
            "explanation": explanation,
        })

    evidence = state.get("evidence") or []
    claims = (state.get("report_result") or {}).get("claims") or []
    if claims:
        evidence_ids = {item.get("id") for item in evidence}
        unsupported = len({
            evidence_id
            for claim in claims
            for evidence_id in claim.get("evidence_ids") or []
            if evidence_id not in evidence_ids
        })
        annotations.append({
            "name": "claims_support",
            "label": None,
            "score": round(1 - unsupported / len(claims), 4),
            "explanation": (
                f"claims {len(claims)}, unsupported {unsupported}, "
                f"evidence {len(evidence)}"
            ),
        })

    if hypotheses:
        counts = {"open": 0, "supported": 0, "rejected": 0}
        for hypothesis in hypotheses:
            status = hypothesis.get("status") or "open"
            counts[status] = counts.get(status, 0) + 1
        decided = counts.get("supported", 0) + counts.get("rejected", 0)
        annotations.append({
            "name": "hypothesis_coverage",
            "label": None,
            "score": round(counts.get("supported", 0) / decided, 4) if decided else None,
            "explanation": (
                f"open {counts.get('open', 0)}, supported {counts.get('supported', 0)}, "
                f"rejected {counts.get('rejected', 0)}"
            ),
        })

    verdict = critic.get("verdict") or ""
    if verdict:
        issue_count = len(critic.get("issues") or [])
        annotations.append({
            "name": "critic_review",
            "label": verdict,
            "score": None,
            "explanation": f"issues={issue_count}" if issue_count else "",
        })
    return annotations


class PhoenixClient:
    def __init__(self, url: str, project: str = "default"):
        self.base_url = url.rstrip("/")
        self.url = self.base_url + "/graphql"
        self.project = project
        self._sdk = None

    def _client(self):
        if self._sdk is None:
            from phoenix.client import Client

            self._sdk = Client(base_url=self.base_url)
        return self._sdk

    def annotate(self, record, state: dict | None = None) -> None:
        self._client().traces.add_trace_annotation(
            trace_id=record.trace_id,
            annotation_name="logicore_agent_quality",
            annotator_kind="LLM" if record.model else "CODE",
            label=record.verdict,
            score=record.score,
            explanation=record.summary,
            metadata={
                "run_id": record.run_id,
                "issues": [item.summary for item in record.issues[:10]],
            },
            identifier=RUN_SUMMARY_IDENTIFIER,
            sync=True,
        )
        if state is not None:
            self.annotate_run_summary(record.trace_id, state)

    def annotate_run_summary(self, trace_id: str, state: dict) -> int:
        """Записать классификацию run; вернуть число записанных аннотаций."""
        written = 0
        for annotation in run_summary_annotations(state):
            if annotation["label"] is None and annotation["score"] is None:
                continue
            payload = {
                "trace_id": trace_id,
                "annotation_name": annotation["name"],
                "annotator_kind": "CODE",
                "explanation": annotation["explanation"],
                "metadata": {},
                "identifier": RUN_SUMMARY_IDENTIFIER,
                "sync": True,
            }
            if annotation["label"] is not None:
                payload["label"] = annotation["label"]
            if annotation["score"] is not None:
                payload["score"] = annotation["score"]
            try:
                self._client().traces.add_trace_annotation(**payload)
                written += 1
            except Exception:
                continue
        return written

    def _query(self, query: str, variables: dict | None = None) -> dict:
        response = httpx.post(self.url, json={"query": query, "variables": variables or {}}, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(str(payload["errors"])[:500])
        return payload["data"]

    def recent_runs(self, limit: int = 1000) -> dict[str, dict]:
        data = self._query("""
            query RecentSpans($project: String!, $limit: Int!) {
              getProjectByName(name: $project) {
                id
                spans(first: $limit, sort: {col: startTime, dir: desc}) {
                  edges { node {
                    spanId name statusCode startTime latencyMs tokenCountTotal
                    context { traceId }
                    metadata
                  } }
                }
              }
            }
        """, {"project": self.project, "limit": limit})
        project = data["getProjectByName"]
        runs = {}
        for edge in project["spans"]["edges"]:
            span = edge["node"]
            metadata = json.loads(span.get("metadata") or "{}")
            run_id = metadata.get("run_id")
            if run_id:
                item = runs.setdefault(run_id, {"trace_id": span["context"]["traceId"], "spans": []})
                item["project_id"] = project["id"]
                item["spans"].append(span)
        return runs

    def trace(self, trace_id: str) -> dict:
        data = self._query("""
            query Trace($traceId: String!) {
              getTraceByOtelId(traceId: $traceId) {
                traceId startTime endTime latencyMs numSpans errorCount
                spans(first: 500) { edges { node {
                  spanId parentId name statusCode statusMessage startTime endTime
                  latencyMs tokenCountTotal tokenCountPrompt tokenCountCompletion
                  attributes metadata
                } } }
              }
            }
        """, {"traceId": trace_id})
        trace = data["getTraceByOtelId"]
        trace["spans"] = [edge["node"] for edge in trace["spans"]["edges"]]
        return trace


def compact_trace(trace: dict, attribute_limit: int = 2500) -> dict:
    """Убрать транспортную обёртку и ограничить вложенный state в spans."""
    spans = []
    for span in trace.get("spans") or []:
        attributes = span.get("attributes") or ""
        spans.append({
            "span_id": span.get("spanId"),
            "parent_id": span.get("parentId"),
            "name": span.get("name"),
            "status": span.get("statusCode"),
            "status_message": (span.get("statusMessage") or "")[:500],
            "start_time": span.get("startTime"),
            "latency_ms": span.get("latencyMs"),
            "tokens": span.get("tokenCountTotal"),
            "attributes": attributes[:attribute_limit],
            "attributes_truncated": len(attributes) > attribute_limit,
        })
    return {
        "trace_id": trace.get("traceId"),
        "latency_ms": trace.get("latencyMs"),
        "num_spans": trace.get("numSpans"),
        "error_count": trace.get("errorCount"),
        "spans": spans,
    }
