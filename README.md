# context-guard

A local MCP proxy that stops oversized tool/MCP output from flooding your context window.

## The problem

Every MCP tool call can dump raw data straight into your ~200K context window. A single database query,
file listing, or web scrape can return tens of thousands of tokens. Context fills fast, response quality
drops, and you burn through usage limits — all for data you never actually needed in full.

## How it works

`context-guard` sits between your MCP client and your downstream MCP servers. It intercepts every tool
response, estimates the token count, and if it exceeds your threshold:

1. Stores the full raw output locally (SQLite).
2. Returns a compact summary + a short handle (`h_xxxx`) instead of the raw payload.
3. Your AI can call `query_fence(handle, query="...")` to fetch only the relevant slice.

Responses under the threshold pass through untouched.

```
MCP client  ──►  context-guard  ──►  downstream MCP servers
                 (fences large        (github, playwright,
                  responses)           everything, ...)
```

## Install

> **Not yet published to PyPI.** Intended install when published:

```bash
pipx install context-guard
# or
uvx context-guard
```

For local development:

```bash
git clone https://github.com/Chriswinther/context-guard
cd context-guard
pip install -e ".[dev]"
```

## Setup

Two files in your project root:

**1. `.mcp.json`** — tells your MCP client to use context-guard as its proxy:

```json
{
  "mcpServers": {
    "context-guard": {
      "command": "context-guard"
    }
  }
}
```

**2. `.context-guard.toml`** — lists the downstream servers to wrap and the fence threshold:

```toml
[fence]
threshold_tokens = 2000

[servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]

[servers.playwright]
command = "npx"
args = ["-y", "@playwright/mcp"]
```

The config file path can be overridden with `CONTEXT_GUARD_CONFIG=path/to/.context-guard.toml`.

## Tools

context-guard exposes four tools to the MCP client, in addition to transparently proxying all
downstream server tools:

| Tool | Description |
|------|-------------|
| `query_fence(handle, query=None, start_line=None, end_line=None)` | Retrieve content from a fenced handle: by keyword search or line range. |
| `run_fenced(command, timeout=120)` | Run a local shell command and fence its output if large. |
| `fetch_fenced(url, timeout=120)` | GET a URL and fence the response body if large. |
| `context_report()` | Show estimated per-tool token usage and savings for the current session. |

## Token counting

Token counts are **estimates** (heuristic: ~4 chars per token). For accurate counts, install
the `tiktoken` extra:

```bash
pip install "context-guard[tiktoken]"
```

## Limitation

context-guard only fences **wrapped downstream MCP servers** and the explicit `run_fenced` /
`fetch_fenced` tools. It does **NOT** fence your MCP client's native built-in tools — for example,
Claude Code's built-in Bash, Read, and Grep tools bypass context-guard entirely and go direct.

## Pro tier (coming soon)

Budget enforcement + per-tool spend attribution dashboard.

## License

MIT — see [LICENSE](LICENSE).
