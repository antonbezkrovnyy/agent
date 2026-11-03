import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logicore-mcp"))

from logicore_mcp.domain.filters import FieldSchema
from logicore_mcp.mcp import tools as tools_mod
from logicore_mcp.mcp.flows import count_events_flow


def test_rate_limit_has_machine_readable_retry_after(monkeypatch):
    decision = SimpleNamespace(allowed=False, retry_after_s=2.0)
    monkeypatch.setattr(tools_mod, "rate_limiter", lambda: SimpleNamespace(check=lambda _: decision))

    @tools_mod._envelope_unexpected
    async def sample():
        return {"ok": True}

    result = asyncio.run(sample())

    assert result["ok"] is False
    assert result["retry_after"] == 3


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
    result = asyncio.run(count_events_flow(
        client,
        time_from="2026-10-31T07:00:00Z",
        time_to="2026-10-31T08:00:00Z",
        filter={"==": [{"var": "action"}, "allow"]},
        schema={"action": FieldSchema(name="action", type="String")},
    ))

    assert result["iql"] == "action = 'allow'"
    assert client.request["data"]["query"]["filters"] == result["iql"]
