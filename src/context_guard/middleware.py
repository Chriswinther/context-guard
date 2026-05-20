"""FastMCP middleware that fences oversized downstream tool results.

The pure helpers (extract_text, fence_payload) are unit-tested directly. The
FenceMiddleware class wires them into FastMCP's on_call_tool hook.
"""
from __future__ import annotations

from typing import Any

from context_guard.fence import fence
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker, savings_readout


def extract_text(content: Any) -> str:
    """Flatten an MCP tool result's content into a single string."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        elif isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "".join(parts)


def fence_payload(
    text: str,
    *,
    tool_name: str,
    store: FenceStore,
    tracker: UsageTracker,
    threshold_tokens: int,
) -> tuple[str, bool]:
    """Return (possibly-fenced text, was_fenced) and record usage."""
    res = fence(text, source=tool_name, store=store, threshold_tokens=threshold_tokens)
    tracker.record(
        tool_name, original_tokens=res.original_tokens, returned_tokens=res.returned_tokens
    )
    return res.text, res.fenced


# --- FastMCP wiring (verified against fastmcp 3.3.1) ---
from fastmcp.server.middleware import Middleware, MiddlewareContext  # noqa: E402
from mcp.types import TextContent  # noqa: E402

# context-guard's own tools return bounded text and must pass through verbatim;
# everything else is a proxied/downstream tool that may be fenced.
_OWN_TOOLS = {"query_fence", "context_report", "run_fenced", "fetch_fenced"}


class FenceMiddleware(Middleware):
    """Intercepts every (proxied/mounted) tool call result; if the flattened text
    exceeds the threshold, replaces the result content with the compact fenced
    distillation + retrieval handle and records the savings.
    """

    def __init__(self, store: FenceStore, tracker: UsageTracker, threshold_tokens: int) -> None:
        self.store = store
        self.tracker = tracker
        self.threshold_tokens = threshold_tokens

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        """Strip the advertised output schema from proxied (downstream) tools.

        A fencing proxy replaces an oversized tool payload with a compact text
        summary, so it cannot honor the downstream tool's declared output schema
        (e.g. the filesystem read tool's schema requires a `content` field).
        Advertising that schema makes a strict MCP client reject every fenced
        reply ("Output validation error: 'content' is a required property").
        For proxied tools the text content is the contract, so we drop the
        schema; context-guard's own tools keep theirs.
        """
        tools = await call_next(context)
        patched = []
        for tool in tools:
            if tool.name in _OWN_TOOLS or getattr(tool, "output_schema", None) is None:
                patched.append(tool)
            else:
                patched.append(tool.model_copy(update={"output_schema": None}))
        return patched

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        result = await call_next(context)

        tool_name = getattr(getattr(context, "message", None), "name", "unknown")

        # Do not re-fence context-guard's own retrieval/report tools — those
        # already return bounded text and must pass through verbatim so the
        # caller can read a handle's contents.
        if tool_name in _OWN_TOOLS:
            return result

        content = getattr(result, "content", result)
        text = extract_text(content)
        new_text, fenced = fence_payload(
            text,
            tool_name=tool_name,
            store=self.store,
            tracker=self.tracker,
            threshold_tokens=self.threshold_tokens,
        )
        if fenced:
            # Append a one-line cumulative savings readout. The tracker already
            # recorded this call (in fence_payload), so the total is current.
            # Only fenced calls carry it — small pass-through results stay clean.
            # Shared with the native fencing tools via savings_readout() so the
            # two code paths cannot drift in wording.
            new_text = f"{new_text}\n\n{savings_readout(self.tracker)}"
            result.content = [TextContent(type="text", text=new_text)]
            # Tools that declare an output schema (e.g. `-> str`) carry
            # structured_content; the MCP client validates that it is present.
            # Overwrite it to the fenced text so the original payload does not
            # leak through structured_content / result.data.
            # Guard for tools whose output_schema is None (string-returning tools
            # always populate structured_content in fastmcp 3.3.1, so this normally fires).
            if getattr(result, "structured_content", None) is not None:
                result.structured_content = {"result": new_text}
        return result
