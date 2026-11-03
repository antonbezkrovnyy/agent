import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logicore-mcp"))

from logicore_mcp.mcp import tools as tools_mod


def test_rate_limit_has_machine_readable_retry_after(monkeypatch):
    decision = SimpleNamespace(allowed=False, retry_after_s=2.0)
    monkeypatch.setattr(tools_mod, "rate_limiter", lambda: SimpleNamespace(check=lambda _: decision))

    @tools_mod._envelope_unexpected
    async def sample():
        return {"ok": True}

    result = asyncio.run(sample())

    assert result["ok"] is False
    assert result["retry_after"] == 3
