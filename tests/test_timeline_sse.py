import asyncio
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logicore-mcp"))

from logicore_mcp.config import Settings
from logicore_mcp.earth.timeline_sse import fetch_timeline


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def test_timeline_sse_handles_json_split_between_network_chunks():
    intervals = [
        {"from": f"2026-10-31T05:{i:02d}:00Z", "to": f"2026-10-31T05:{i:02d}:59Z", "value": 0}
        for i in range(60)
    ]
    init = json.dumps({"intervals": intervals}).encode()
    chunks = [
        b"event: init\ndata: " + init[:3935],
        init[3935:] + b"\n\nevent: chunk\ndata: "
        + json.dumps({"from": intervals[0]["from"], "to": intervals[0]["to"], "value": 42}).encode()
        + b"\n\nevent: done\ndata: {}\n\n",
    ]

    def handler(request):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=ChunkedStream(chunks))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
            settings = Settings(url="http://test", user="test", password="test", ws_wait=1)
            return await fetch_timeline(client, settings, "token", {"data": {"query": {}}})

    result = asyncio.run(run())

    assert result["status"] == "finished"
    assert len(result["data"]) == 60
    assert result["data"][0]["value"] == 42
