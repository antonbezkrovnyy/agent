import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logicore-mcp"))

from logicore_mcp.domain.filters import FieldSchema
from logicore_mcp.domain.projection import build_search_response
from logicore_mcp.earth.schemas import EventsResponse
from logicore_mcp.mcp.extract import extract_data_flow

COLUMNS = ("uuid", "timestamp", "message")
SCHEMA = {
    "uuid": FieldSchema(name="uuid", type="String"),
    "message": FieldSchema(name="message", type="String"),
    "action": FieldSchema(name="action", type="String"),
}


def rows(count):
    return [
        {"uuid": f"event-{i}", "timestamp": f"2026-10-31T08:00:{i % 60:02d}Z", "message": f"row {i}"}
        for i in range(count)
    ]


class FakeClient:
    def __init__(self, data, repeat_first=False):
        self.data = data
        self.repeat_first = repeat_first
        self.pages = []
        self.requests = []

    async def user_context(self):
        return {}

    def tenant_ids(self):
        return [1]

    async def search_events(self, request):
        self.requests.append(request)
        query = request["data"]
        page = 1 if self.repeat_first else query["pagination"]["page"]
        per_page = query["pagination"]["perPage"]
        self.pages.append(page)
        start = (page - 1) * 100
        columns = query["query"]["columns"]
        data = [
            {key: value for key, value in row.items() if key in columns}
            for row in self.data[start:start + per_page]
        ]
        return EventsResponse(
            result=True,
            message="",
            data=data,
            meta={
                "current_page": page,
                "last_page": 1,
                "per_page": per_page,
                "total": 0,
                "from": 1,
                "to": len(data),
                "schema": [{"name": name} for name in columns],
            },
        )


def extract(client, max_rows=1000, page=1, filter=None, columns=COLUMNS, **options):
    return asyncio.run(extract_data_flow(
        client,
        time_from="2026-10-31T07:00:00Z",
        time_to="2026-10-31T08:00:00Z",
        filter=filter,
        columns=columns,
        max_rows=max_rows,
        page=page,
        order="asc",
        default_columns=COLUMNS,
        schema=SCHEMA,
        **options,
    ))


def test_extract_data_reads_all_pages():
    client = FakeClient(rows(250))

    result = extract(client)

    assert result["returned_rows"] == 250
    assert result["complete"] is True
    assert result["next_page"] is None
    assert client.pages == [1, 2, 3]


def test_extract_data_marks_limit_and_continuation():
    client = FakeClient(rows(250))

    result = extract(client, max_rows=200)

    assert result["returned_rows"] == 200
    assert result["complete"] is False
    assert result["next_page"] == 3


def test_extract_data_resumes_from_next_page():
    client = FakeClient(rows(250))

    result = extract(client, max_rows=200, page=3)

    assert result["returned_rows"] == 50
    assert result["events"][0]["uuid"] == "event-200"
    assert result["events"][-1]["uuid"] == "event-249"
    assert result["complete"] is True
    assert result["next_page"] is None
    assert client.pages == [3]


def test_extract_data_stops_on_repeated_page():
    client = FakeClient(rows(250), repeat_first=True)

    result = extract(client)

    assert result["returned_rows"] == 100
    assert result["complete"] is False
    assert "повторила" in result["hint"]


def test_search_response_exposes_next_page():
    result = build_search_response(
        rows=rows(2),
        meta_total=0,
        meta_schema=[{"name": name} for name in COLUMNS],
        requested_columns=COLUMNS,
        window={"from": "a", "to": "b", "source": "explicit"},
        filter_applied=None,
        page=2,
        per_page=2,
    )

    assert result["has_more"] is True
    assert result["next_page"] == 3
    assert result["truncated"] is True


def test_extract_data_returns_executed_iql():
    result = extract(
        FakeClient(rows(1)),
        filter="action = 'allow'",
    )

    assert result["iql"] == "action = 'allow'"



def test_extract_without_uuid_does_not_treat_equal_rows_as_repeated_page():
    data = rows(150)
    for row in data:
        row["message"] = "identical"

    result = extract(FakeClient(data), max_rows=200, columns=("message",))

    assert result["returned_rows"] == 150
    assert result["complete"] is True
    assert result["next_page"] is None



def test_extract_forwards_native_options_and_marks_query_limit():
    client = FakeClient(rows(250))
    order_by = [{"column": "message", "action": "ASC"}]
    groups = [{"column": "action", "value": "allow"}]

    result = extract(
        client,
        max_rows=1000,
        columns=COLUMNS,
        groups=groups,
        order_by=order_by,
        query_limit=150,
    )

    assert result["returned_rows"] == 150
    assert result["complete"] is False
    assert result["next_page"] is None
    assert result["query_limit"] == 150
    assert result["groups"] == groups
    assert result["order"] == order_by
    assert client.requests[0]["data"]["query"]["groups"] == groups
    assert client.requests[0]["data"]["query"]["order"] == order_by
    assert client.requests[0]["data"]["query"]["limit"] == 150
