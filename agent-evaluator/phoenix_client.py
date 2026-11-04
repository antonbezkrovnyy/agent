"""Read-only доступ к Phoenix GraphQL."""

import json

import httpx


class PhoenixClient:
    def __init__(self, url: str, project: str = "default"):
        self.base_url = url.rstrip("/")
        self.url = self.base_url + "/graphql"
        self.project = project
        self._sdk = None

    def annotate(self, record) -> None:
        if self._sdk is None:
            from phoenix.client import Client

            self._sdk = Client(base_url=self.base_url)
        self._sdk.traces.add_trace_annotation(
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
            identifier="run-v1",
            sync=True,
        )

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
