import re

import pytest
from fastmcp import Client, FastMCP

from context_guard.server import build_server
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker


def make_downstream() -> FastMCP:
    down = FastMCP("downstream")

    @down.tool
    def big_list() -> str:
        return "x" * 40000

    @down.tool
    def small() -> str:
        return "hi"

    return down


@pytest.mark.asyncio
async def test_large_downstream_output_is_fenced_and_retrievable():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    downstream = make_downstream()
    server = build_server(
        downstream_backends={"down": downstream},
        store=store,
        tracker=tracker,
        threshold_tokens=2000,
    )

    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
        assert any(name.endswith("big_list") for name in tools)
        assert "query_fence" in tools
        assert "context_report" in tools

        big_tool = next(n for n in tools if n.endswith("big_list"))
        result = await client.call_tool(big_tool, {})
        text = result.content[0].text
        assert "query_fence" in text  # fenced

        handle = re.search(r"h_[0-9a-f]+", text).group(0)
        retrieved = await client.call_tool(
            "query_fence", {"handle": handle, "start_line": 0, "end_line": 0}
        )
        assert "x" in retrieved.content[0].text

        report = await client.call_tool("context_report", {})
        assert "saved" in report.content[0].text.lower()
