import json
import re

import pytest
from fastmcp import Client, FastMCP

from context_guard.server import build_server
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker

# Unique marker string used in leak-guard tests.
_LEAK_MARKER = "XBIGMARKER"
# 2000 repetitions = 20 000 chars / ~5 000 tokens — well over the 2 000-token threshold.
_RAW_PAYLOAD = _LEAK_MARKER * 2000


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


@pytest.mark.asyncio
async def test_fenced_result_leaks_no_raw_payload_via_any_channel():
    """The product's core guarantee: the raw payload must NOT survive in any
    MCP result channel visible to the caller (content, structured_content, data).

    A unique 20 000-char marker is used so a false negative (marker appearing in
    the distilled summary) is essentially impossible.
    """
    down = FastMCP("leak_probe")

    @down.tool
    def big_payload() -> str:
        return _RAW_PAYLOAD

    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    server = build_server(
        downstream_backends={"probe": down},
        store=store,
        tracker=tracker,
        threshold_tokens=2000,
    )

    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
        tool_name = next(n for n in tools if n.endswith("big_payload"))
        result = await client.call_tool(tool_name, {})

        # --- content channel ---
        content_text = result.content[0].text if result.content else ""
        assert _RAW_PAYLOAD not in content_text, (
            f"Raw payload leaked via content[0].text (length {len(content_text)})"
        )
        # Fenced reply must be present
        assert "query_fence" in content_text, "Expected fenced reply in content[0].text"
        assert re.search(r"h_[0-9a-f]+", content_text), "Expected handle in content[0].text"

        # --- structured_content channel ---
        sc = result.structured_content  # dict | None
        sc_str = json.dumps(sc) if sc is not None else ""
        assert _RAW_PAYLOAD not in sc_str, (
            f"Raw payload leaked via structured_content (length {len(sc_str)})"
        )

        # --- data channel (parsed from structured_content by fastmcp client) ---
        data = result.data  # Any
        data_str = str(data) if data is not None else ""
        assert _RAW_PAYLOAD not in data_str, (
            f"Raw payload leaked via result.data (length {len(data_str)})"
        )

        # --- full serialisation (belt-and-suspenders) ---
        try:
            full = result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)
        except Exception:
            full = str(result)
        assert _RAW_PAYLOAD not in full, (
            f"Raw payload leaked in full result serialisation (length {len(full)})"
        )
