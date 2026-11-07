import asyncio
import inspect
import os
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logicore-mcp"))

from logicore_mcp.domain.projection import build_activity
from logicore_mcp.earth.client import _business_error
from logicore_mcp.earth.schemas import build_count_request, build_events_request, build_timeline_request
from logicore_mcp.mcp import tools as tools_mod
from logicore_mcp.mcp.contracts import FILTER_PARAM
from logicore_mcp.mcp.flows import _search_activity, count_events_flow, field_fill_rates_flow


def test_rate_limit_has_machine_readable_retry_after(monkeypatch):
    decision = SimpleNamespace(allowed=False, retry_after_s=2.0)
    monkeypatch.setattr(tools_mod, "rate_limiter", lambda: SimpleNamespace(check=lambda _: decision))

    @tools_mod._envelope_unexpected
    async def sample():
        return {"ok": True}

    result = asyncio.run(sample())

    assert result["ok"] is False
    assert result["retry_after"] == 3


def test_windowed_tools_accept_iql_instead_of_jsonlogic_filter():
    tool_names = (
        "search_events",
        "extract_data",
        "count_events",
        "aggregate_events",
        "timeline",
        "top_values",
        "top_values_batch",
        "field_fill_rates",
        "duplicates",
    )

    for name in tool_names:
        parameters = inspect.signature(getattr(tools_mod, name)).parameters
        assert "iql" in parameters
        assert "filter" not in parameters


def test_iql_contract_trims_expression_and_rejects_blank():
    adapter = TypeAdapter(FILTER_PARAM)
    expression = "positionCaseInsensitive(message, 'error') > 0 AND has(tags, 'unparsed')"

    assert adapter.validate_python(f"  {expression}\n") == expression
    assert adapter.validate_python(None) is None
    with pytest.raises(ValidationError, match="iql должен быть непустой строкой"):
        adapter.validate_python("  ")


def test_count_returns_the_same_iql_that_was_executed():
    class Client:
        request = None

        async def user_context(self):
            return {}

        def tenant_ids(self):
            return [1]

        async def count_events(self, request):
            self.request = request
            return 7

    client = Client()
    expression = "positionCaseInsensitive(message, 'error') > 0 AND has(tags, 'unparsed')"
    result = asyncio.run(count_events_flow(
        client,
        time_from="2026-10-31T07:00:00Z",
        time_to="2026-10-31T08:00:00Z",
        filter=expression,
        schema={},
    ))

    assert result["iql"] == expression
    assert client.request["data"]["query"]["filters"] == expression



def test_activity_at_limit_is_not_claimed_complete():
    items = [{"timestamp": f"2026-10-31T05:{i:02d}:00Z", "total_rows_count": 1} for i in range(20)]

    assert build_activity(items)["complete"] is False
    assert build_activity(items[:19])["complete"] is True


def test_event_request_has_deterministic_tie_breaker():
    request = build_events_request(
        filters=None,
        time_from=datetime(2026, 10, 31, 7, tzinfo=UTC),
        time_to=datetime(2026, 10, 31, 8, tzinfo=UTC),
        tenant_ids=[1],
        columns=("timestamp", "message"),
    )

    assert request["data"]["query"]["order"] == [
        {"column": "timestamp", "action": "DESC"},
        {"column": "uuid", "action": "DESC"},
    ]


def test_field_fill_rates_rejects_offset_beyond_schema():
    class Client:
        async def user_context(self):
            raise AssertionError("MARS must not be called for an invalid offset")

    result = asyncio.run(field_fill_rates_flow(
        Client(),
        time_from="2026-10-31T07:00:00Z",
        time_to="2026-10-31T08:00:00Z",
        filter=None,
        schema={"a": None, "b": None},
        offset=3,
    ))

    assert result["ok"] is False
    assert "offset=3" in result["error"]
    assert "0 до 2" in result["hint"]


def test_field_fill_rates_accepts_offset_at_schema_end():
    class Client:
        async def user_context(self):
            return {}

        def tenant_ids(self):
            return [1]

    result = asyncio.run(field_fill_rates_flow(
        Client(),
        time_from="2026-10-31T07:00:00Z",
        time_to="2026-10-31T08:00:00Z",
        filter=None,
        schema={"a": None, "b": None},
        offset=2,
    ))

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["fields_scanned"] == 0



def test_bad_iql_error_contains_recovery_hint():
    error = _business_error({"message": "Bad sql string"})

    assert "SQL/IQL" in str(error)
    assert "WHERE-условие" in error.hint



def test_partial_coverage_is_visible_at_top_level():
    from logicore_mcp.earth.scheduler import PartialResult

    class Client:
        async def user_context(self):
            return {}

        def tenant_ids(self):
            return [1]

        async def count_events(self, request):
            return PartialResult(7, {"complete": False, "covered_until": "2026-10-31T07:30:00Z"})

    result = asyncio.run(count_events_flow(
        Client(),
        time_from="2026-10-31T07:00:00Z",
        time_to="2026-10-31T08:00:00Z",
        filter=None,
        schema={},
    ))

    assert result["ok"] is True
    assert result["complete"] is False
    assert result["coverage"]["complete"] is False



def test_native_query_parameters_are_in_public_schemas():
    expected = {
        "search_events": {"groups", "order_by", "query_limit"},
        "extract_data": {"groups", "order_by", "query_limit"},
        "count_events": {"groups", "query_limit"},
        "timeline": {"groups"},
    }

    for name, parameters in expected.items():
        schema = inspect.signature(getattr(tools_mod, name)).parameters
        assert parameters <= schema.keys()


def test_count_forwards_groups_and_marks_query_limit():
    class Client:
        request = None

        async def user_context(self):
            return {}

        def tenant_ids(self):
            return [1]

        async def count_events(self, request):
            self.request = request
            return 7

    client = Client()
    groups = [{"column": "action", "value": "allow"}]
    result = asyncio.run(count_events_flow(
        client,
        time_from="2026-10-31T07:00:00Z",
        time_to="2026-10-31T08:00:00Z",
        filter=None,
        schema={"action": None},
        groups=groups,
        query_limit=5,
    ))

    assert result["complete"] is False
    assert result["query_limit"] == 5
    assert result["groups"] == groups
    assert client.request["data"]["query"]["groups"] == groups
    assert client.request["data"]["query"]["limit"] == 5


def test_timeline_request_forwards_groups():
    groups = [{"column": "action", "value": None}]
    request = build_timeline_request(
        filters=None,
        time_from=datetime(2026, 10, 31, 7, tzinfo=UTC),
        time_to=datetime(2026, 10, 31, 8, tzinfo=UTC),
        tenant_ids=[1],
        groups=groups,
    )

    assert request["data"]["query"]["groups"] == groups


def test_scheduler_merge_honours_multi_column_order():
    from logicore_mcp.earth.scheduler import merge_events_responses
    from logicore_mcp.earth.schemas import EventsResponse

    def response(action, uuid):
        return EventsResponse(
            result=True,
            data=[{"action": action, "uuid": uuid, "timestamp": "2026-10-31T07:00:00Z"}],
            meta={
                "current_page": 1,
                "last_page": 1,
                "per_page": 20,
                "total": 1,
                "from": 1,
                "to": 1,
                "schema": [],
            },
        )

    result = merge_events_responses(
        [response("b", "a"), response("a", "b")],
        limit=10,
        order="desc",
        order_by=[{"column": "action", "action": "ASC"}],
    )

    assert [row["action"] for row in result.data] == ["a", "b"]



def test_activity_uses_iql_and_all_group_conditions():
    class Client:
        request = None

        async def group_by(self, request):
            self.request = request
            return [{"timestamp": "2026-10-31T07:00:00Z", "total_rows_count": 1}]

    client = Client()
    groups = [
        {"column": "uuid", "value": "event-1"},
        {"column": "tags", "value": ["a", "b"]},
        {"column": "object", "value": None},
    ]
    result = asyncio.run(_search_activity(
        client,
        iql="action = 'login'",
        start=datetime(2026, 10, 31, 7, tzinfo=UTC),
        end=datetime(2026, 10, 31, 8, tzinfo=UTC),
        tenant_ids=[1],
        has_rows=True,
        groups=groups,
    ))

    filters = client.request["data"]["query"]["filters"]
    assert "(action = 'login')" in filters
    assert "uuid = 'event-1'" in filters
    assert "tags = ['a', 'b']" in filters
    assert "object IS NULL" in filters
    assert result["complete"] is True
