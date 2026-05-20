"""A minimal STATEFUL stdio MCP server used as a regression fixture.

It stores a value bound to the current MCP session -- exactly how Playwright's
"current page" is bound to its session, not to the OS process. So `remember`
then `recall` is the direct analog of Playwright `navigate` then `snapshot`:

  - If the proxy reuses ONE downstream session across calls, recall() returns
    what remember() stored.
  - If the proxy opens a fresh session (and subprocess) per call, recall()
    returns "<EMPTY>" -- the failure that returned about:blank after navigate.

Run via stdio: `python stateful_counter_server.py`.
"""
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_context

mcp = FastMCP("stateful-probe")

# State bound to the identity of the downstream session object.
_per_session: dict[int, str] = {}


@mcp.tool
def remember(value: str) -> str:
    """Store a value bound to the CURRENT session (like navigate sets the page)."""
    _per_session[id(get_context().session)] = value
    return f"stored:{value}"


@mcp.tool
def recall() -> str:
    """Read the value stored in the current session (like snapshot reads it)."""
    return _per_session.get(id(get_context().session), "<EMPTY>")


if __name__ == "__main__":
    mcp.run()
