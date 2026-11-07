import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logicore-mcp"))

from logicore_mcp.domain.filters import FieldSchema
from logicore_mcp.earth.scheduler import PartialResult
from logicore_mcp.mcp.flows import aggregate_events_flow, field_lookup_flow, search_fields_flow


SCHEMA = {
    "src_ip": FieldSchema(name="src_ip", type="String"),
    "dst_ip": FieldSchema(name="dst_ip", type="String"),
    "action": FieldSchema(name="action", type="String"),
}


def test_search_fields_finds_names_without_event_calls():
    result = search_fields_flow(query="src", limit=10, schema=SCHEMA)

    assert result["ok"] is True
    assert result["fields"][0]["field"] == "src_ip"
    assert result["fields"][0]["type"] == "String"
    assert result["truncated"] is False


def test_field_lookup_returns_valid_fields_alongside_unknown_names():
    result = asyncio.run(field_lookup_flow(
        fields=["src_ip", "src_ippp"],
        schema=SCHEMA,
    ))

    assert result["ok"] is True
    assert result["complete"] is False
    assert result["invalid"] == 1
    assert result["fields"][0]["field"] == "src_ip"
    assert result["fields"][0]["ok"] is True
    assert result["fields"][0]["type"] == "String"
    assert result["fields"][1]["ok"] is False
    assert "src_ip" in result["fields"][1]["hint"]


class AggregateClient:
    def __init__(self, rows):
        self.rows = rows
        self.request = None

    async def user_context(self):
        return {}

    def tenant_ids(self):
        return [1]

    async def group_by(self, request):
        self.request = request
        return self.rows


def test_aggregate_events_uses_composite_group_by_and_preserves_iql():
    client = AggregateClient([
        {"src_ip": "10.0.0.1", "dst_ip": "10.0.0.2", "total_rows_count": 3},
        {"src_ip": "10.0.0.1", "dst_ip": "10.0.0.3", "total_rows_count": 2},
    ])

    result = asyncio.run(aggregate_events_flow(
        client,
        dimensions=["src_ip", "dst_ip", "src_ip"],
        time_from="2026-11-06T05:00:00Z",
        time_to="2026-11-06T05:30:00Z",
        filter="action = 'deny'",
        limit=100,
        schema=SCHEMA,
    ))

    query = client.request["data"]["query"]
    assert query["columns"] == ["src_ip", "dst_ip"]
    assert query["filters"] == "action = 'deny'"
    assert result["groups_count"] == 2
    assert result["groups_total"] == 2
    assert result["events_in_groups"] == 5
    assert result["complete"] is True


def test_aggregate_events_does_not_claim_exact_distinct_at_ceiling():
    client = AggregateClient([
        {"src_ip": "10.0.0.1", "total_rows_count": 3},
        {"src_ip": "10.0.0.2", "total_rows_count": 2},
    ])

    result = asyncio.run(aggregate_events_flow(
        client,
        dimensions=["src_ip"],
        time_from="2026-11-06T05:00:00Z",
        time_to="2026-11-06T05:30:00Z",
        filter=None,
        limit=2,
        schema=SCHEMA,
    ))

    assert result["complete"] is False
    assert result["ceiling_reached"] is True
    assert result["groups_lower_bound"] == 2
    assert result["groups_total"] is None


def test_aggregate_events_marks_partial_window_coverage():
    client = AggregateClient(PartialResult(
        [{"src_ip": "10.0.0.1", "total_rows_count": 3}],
        {"complete": False, "covered_until": "2026-11-06T05:15:00Z"},
    ))

    result = asyncio.run(aggregate_events_flow(
        client,
        dimensions=["src_ip"],
        time_from="2026-11-06T05:00:00Z",
        time_to="2026-11-06T05:30:00Z",
        filter=None,
        limit=100,
        schema=SCHEMA,
    ))

    assert result["complete"] is False
    assert result["groups_total"] is None
    assert result["coverage"]["complete"] is False
