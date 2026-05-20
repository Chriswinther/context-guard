import json
import re
import sys
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from context_guard.config import Config, ServerSpec
from context_guard.server import build_server
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker

# A stateful stdio MCP server: stores a value bound to the MCP session, the way
# Playwright's "current page" is bound to its session (remember=navigate, recall=snapshot).
_STATEFUL_FIXTURE = str(Path(__file__).parent / "fixtures" / "stateful_counter_server.py")

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


def _two_server_specs() -> dict:
    """Two stdio downstream servers, MCPConfig shape. Multiple servers is the
    shape that exposed the bug (the deployed config was filesystem + playwright);
    a single-server config happens to keep its subprocess alive and so does NOT
    reproduce -- this MUST stay multi-server."""
    return {
        "mcpServers": {
            "a": {"command": sys.executable, "args": [_STATEFUL_FIXTURE]},
            "b": {"command": sys.executable, "args": [_STATEFUL_FIXTURE]},
        }
    }


def _build_via_config(store, tracker):
    cfg = Config(
        threshold_tokens=2000,
        servers=[
            ServerSpec(name="a", command=sys.executable, args=[_STATEFUL_FIXTURE]),
            ServerSpec(name="b", command=sys.executable, args=[_STATEFUL_FIXTURE]),
        ],
    )
    return build_server(config=cfg, store=store, tracker=tracker, threshold_tokens=2000)


def _build_via_backends_dict(store, tracker):
    return build_server(
        downstream_backends=_two_server_specs(),
        store=store,
        tracker=tracker,
        threshold_tokens=2000,
    )


@pytest.mark.parametrize(
    "build_server_fn",
    [_build_via_config, _build_via_backends_dict],
    ids=["config_path", "mcpconfig_dict_path"],
)
@pytest.mark.asyncio
async def test_stateful_downstream_keeps_session_state_across_calls(build_server_fn):
    """Regression: a STATEFUL downstream MCP must keep its session state across
    separate proxied tool calls. `remember` then `recall` is the exact analog of
    Playwright `navigate` then `snapshot`.

    Both proxy-construction entry points (production `config=` and a pre-built
    `downstream_backends=` MCPConfig dict) spawn downstream subprocesses, so both
    must be stateful. With a stateless client factory, an MCPConfig of multiple
    servers spawns a fresh subprocess + session per call, so `recall` after
    `remember` returned "<EMPTY>" -- the same way `snapshot` saw about:blank
    after `navigate`.
    """
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    server = build_server_fn(store, tracker)

    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
        # Both remember and recall must hit the SAME downstream server ("a").
        remember = next(n for n in tools if n.startswith("a") and n.endswith("remember"))
        recall = next(n for n in tools if n.startswith("a") and n.endswith("recall"))
        await client.call_tool(remember, {"value": "HELLO"})
        recalled = (await client.call_tool(recall, {})).content[0].text.strip()

    assert recalled == "HELLO", (
        f"stateful downstream lost session state across calls: recall returned "
        f"{recalled!r} after remember (fresh session spawned per call)"
    )


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
async def test_fenced_call_appends_cumulative_savings_readout():
    """Every call context-guard actually saves tokens on must end with a one-line
    cumulative readout: '🛡️ tokens saved by context-guard: <N>'. Small/pass-through
    calls get no readout. The number is the running session total and accumulates."""
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    server = build_server(
        downstream_backends={"down": make_downstream()},
        store=store,
        tracker=tracker,
        threshold_tokens=2000,
    )

    def readout_total(text: str) -> int | None:
        m = re.search(r"tokens saved by context-guard: ([\d,]+)", text)
        return int(m.group(1).replace(",", "")) if m else None

    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
        big = next(n for n in tools if n.endswith("big_list"))
        small = next(n for n in tools if n.endswith("small"))

        # Small/pass-through call: no readout.
        small_text = (await client.call_tool(small, {})).content[0].text
        assert "context-guard" not in small_text
        assert readout_total(small_text) is None

        # First fenced call: readout present, positive, equals session total.
        first = (await client.call_tool(big, {})).content[0].text
        first_total = readout_total(first)
        assert first_total is not None and first_total > 0
        assert first_total == tracker.report()["_total"]["saved"]

        # Second fenced call: cumulative total grows.
        second = (await client.call_tool(big, {})).content[0].text
        second_total = readout_total(second)
        assert second_total is not None and second_total > first_total
        assert second_total == tracker.report()["_total"]["saved"]


@pytest.mark.asyncio
async def test_proxied_object_schema_tool_does_not_break_client_validation():
    """Regression for Bug A: a downstream tool that declares an output schema
    requiring a field other than 'result' (the real filesystem read_text_file
    requires 'content') must not make a strict MCP client reject the fenced
    reply. context-guard fences by replacing the payload with text, so it must
    NOT advertise an output schema it cannot honor for proxied tools.

    Before the fix the proxy surfaced the downstream outputSchema; the fenced
    structured_content {'result': ...} then failed the client's validation
    against {required: ['content']} (Claude Code: 'Output validation error:
    content is a required property').
    """
    from pydantic import BaseModel

    class FileRead(BaseModel):
        content: str

    down = FastMCP("fs")

    @down.tool
    def read_doc() -> FileRead:
        return FileRead(content="z" * 40000)

    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    server = build_server(
        downstream_backends={"fs": down},
        store=store,
        tracker=tracker,
        threshold_tokens=2000,
    )

    async with Client(server) as client:
        tools = await client.list_tools()
        proxied = next(t for t in tools if t.name.endswith("read_doc"))
        # The fencing proxy must not advertise a structured output schema for a
        # proxied tool — the fenced text content is the contract.
        assert proxied.outputSchema is None, (
            f"proxied tool still advertises outputSchema: {proxied.outputSchema!r}"
        )

        result = await client.call_tool(proxied.name, {})
        text = result.content[0].text
        assert "query_fence" in text  # fenced
        assert "z" * 40000 not in text  # no raw leak


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
