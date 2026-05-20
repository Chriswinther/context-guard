# context-guard — Design Spec

> Status: approved for planning · Date: 2026-05-20 · Owner: chris

## Summary

`context-guard` is a **local stdio MCP aggregating proxy** that stops Claude Code
(and other MCP clients) from blowing their context window on oversized tool
output. Claude connects to `context-guard` only; it spawns the user's real
downstream MCP servers as child processes, re-exposes their tools (namespaced),
and **auto-fences** any response larger than a threshold — returning a compact,
deterministic distillation plus a retrieval handle, while storing the full raw
output locally for on-demand retrieval.

It ships **freemium**: a free, MIT-licensed OSS core (the fencer + proxy) and a
paid **Pro** tier (budget enforcement + spend attribution) unlocked by an
**offline Ed25519-signed license key** (no phone-home).

### Why this product, and why this shape

- The most-validated, MCP-addressable Claude-dev pain is tool/MCP output dumping
  raw data into the 200K context window (see research note
  `2026-05-20-errors-in-claude-development-we-could-fix-with-an-mcp-full.md`).
- The leading free tool (Context Mode, 570 HN pts) **cannot intercept other MCP
  servers' responses** and gives **zero token-spend visibility**. The proxy
  pattern is the moat: it is the only design that fences *other servers'* output.
- A hosted, stateless-HTTP mcpize listing (how `edgar-insider` ships) **cannot**
  do this — it can neither see other servers' traffic nor run the user's local
  commands. Hence context-guard is a **local** tool, distributed via PyPI, not a
  mcpize listing. This was an explicit, accepted trade-off.

## Non-goals (YAGNI)

- Not a hosted mcpize listing. Distribution is PyPI (pip/pipx/uvx).
- Does **not** intercept Claude Code's *native* `Bash`/`Read`/`Grep` output —
  those are not MCP traffic. Only wrapped MCP-server output and explicit
  `run_fenced`/`fetch_fenced` calls are fenced.
- No LLM-based summarization in v1 (deterministic distill only — zero API cost,
  fully offline/private). May become a Pro option later.
- No team/shared-config or hosted dashboard in v1 (Pro M2 may add later).

## Architecture

A `FastMCP` server (matches the `edgar-mcp` stack) running over **stdio** by
default. On startup it builds a proxy over the configured downstream servers and
registers both the aggregated downstream tools and its own tools.

### Setup the user performs

`.mcp.json` (the MCP client config) points at context-guard:

```json
{ "mcpServers": { "context-guard": { "command": "context-guard" } } }
```

`.context-guard.toml` (read by context-guard) lists the servers to wrap:

```toml
[fence]
threshold_tokens = 2000      # responses estimated above this get fenced

[servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]

[servers.playwright]
command = "npx"
args = ["@playwright/mcp"]
```

### Components (each: one purpose, independently testable)

| Module | Responsibility | Depends on |
|---|---|---|
| `server.py` | FastMCP entry; assemble proxy + own tools; run stdio; `main()` | proxy, tools, config |
| `proxy.py` | spawn/manage downstream servers (stdio clients); aggregate + namespace tools; forward calls through the fencer | config, fence |
| `fence.py` | measure size; decide fence vs passthrough; assemble the fenced reply | distill, store, tokens, budget |
| `distill.py` | deterministic per-type distillers (JSON, logs/text, HTML, fallback) | tokens |
| `store.py` | SQLite + FTS5: `put(raw)→handle`, `query(handle, query\|lines)`, TTL/size prune | — |
| `tokens.py` | token **estimate** (heuristic default; optional `tiktoken`) | — |
| `config.py` | load + validate `.context-guard.toml` | — |
| `tools.py` | `query_fence`, `run_fenced`, `fetch_fenced`, `context_report` | store, fence |
| `budget.py` *(Pro)* | per-tool/session token accounting + enforcement | license |
| `license.py` *(Pro)* | Ed25519 verify / `activate` / gate | — |

Project layout mirrors `edgar-mcp` (src layout, `pyproject.toml` with a
`[project.scripts]` entry `context-guard = "context_guard.server:main"`, pytest).

## Data flow

1. **Startup** — read config → spawn each child server → MCP handshake → collect
   tool lists → register namespaced downstream tools (`<server>__<tool>`) + own
   tools on the FastMCP server → `mcp.run()` (stdio).
2. **Wrapped tool call** — Claude calls `github__search_issues` → proxy routes to
   the github child → gets the response → `fence.measure()`:
   - over threshold → `store.put(raw)→handle`, `distill()→summary`, return
     `summary + handle + "call query_fence(handle, ...) for specifics"`;
   - at/under threshold → passthrough unchanged.
   - In both cases, update the per-tool token tally.
3. **`query_fence(handle, query=None, lines=None)`** — return the matching slice
   from the store (FTS query or line/byte range), capped to a safe size.
4. **`run_fenced(command)` / `fetch_fenced(url)`** — execute **locally**
   (subprocess / httpx) with a hard timeout → same fence path. Works because the
   proxy runs on the user's machine.
5. **`context_report()`** — free: per-tool session token totals; Pro: full
   attribution + budget status + burn projection.
6. **Budget (Pro)** — if enabled and a call would push the session over a cap,
   fence aggressively or block-with-warning instead of returning raw.

## Fencing & distillation

- Threshold-gated; default ≈2000 estimated tokens, configurable.
- Deterministic distillers, each returning a compact view **plus** the handle:
  - **JSON/dict** → key shape + types, array lengths, first/last *K* elements,
    total size.
  - **Logs/plain text** → head *N* + tail *N* lines + total line/char count.
  - **HTML** → tag-strip to text, then text-distill.
  - **Fallback** → head/tail character window.
- **Nothing is lost** — the full raw output is always retrievable via
  `query_fence`. This directly answers the "incomplete data → hallucinations"
  objection raised on the HN thread.
- **Token estimate** is an approximation (Anthropic's exact BPE is not public);
  used only for thresholds and reporting. Documented as an estimate. Heuristic by
  default; uses `tiktoken` (cl100k) if it is installed.

## Store

- SQLite file in a per-session cache dir (e.g. `~/.context-guard/cache/`), with an
  FTS5 virtual table for `query_fence` keyword retrieval; line/byte-range slicing
  for positional retrieval.
- Handles are short ids (`h_<6hex>`). The stdio proxy process is long-lived for
  the session, so handles remain valid for the session.
- TTL/size cap: prune oldest entries when over a configured size; a query against
  a pruned handle returns an explicit "expired — re-run the tool" message.

## Free vs Pro

- **Free (M1):** proxy + auto-fence + `query_fence` + `run_fenced` +
  `fetch_fenced` + basic `context_report` (session totals). This is the adoption
  driver — the lever that matters most is distribution.
- **Pro (M2):** budget **enforcement** (hard per-tool/session caps →
  auto-aggressive-fence or block-with-warning), detailed per-tool attribution +
  export, custom distill profiles. Later: team/shared config.

## Licensing (Pro)

- **Ed25519, offline, no phone-home** — mandatory for a tool that sits in the
  user's dev pipeline.
- Ship an embedded public key. A license key is
  `base64url(payload).base64url(signature)`, payload `{email, tier, issued,
  expires?}`.
- `context-guard activate <key>` verifies the signature and stores
  `~/.context-guard/license.json`. Pro modules check `license.is_pro()` at startup
  and **degrade gracefully** to free if the key is absent/invalid/expired.
- Keys are minted with a privately-held key via `scripts/mint_license.py`. Sales
  via Polar / Lemon Squeezy / Stripe; manual minting per sale is acceptable for
  v1, webhook automation later.

## Error handling

- A downstream server that fails to start is marked unavailable; the proxy and
  the other servers stay up. Calling an unavailable server's tool returns a clear
  error.
- `run_fenced` / `fetch_fenced` always use a **hard timeout** (default 120s) and
  capture stdout/stderr/exit — no silent hangs (same lesson as the trading bot's
  no-timeout hang incident).
- Unknown/expired handle in `query_fence` → explicit, actionable error.
- Store over size cap → prune oldest.
- Config errors fail fast at startup with clear messages.

## Testing (pytest, like edgar-mcp)

- **Unit:** each distiller (JSON/text/HTML/fallback); token-estimate monotonicity;
  store `put`/`query`/`prune`; license verify (valid / tampered / expired /
  wrong-key); budget accounting + enforcement; config parsing + validation.
- **Integration:** FastMCP in-memory — a tiny echo downstream server returns a
  huge payload → route through the proxy → assert it is fenced (small reply +
  handle) → `query_fence` returns the correct slice → `context_report` shows the
  tally.

## Milestones

- **M1 — free core:** proxy + fence + retrieval tools + basic report; publish to
  PyPI with a README demonstrating the wrap-and-save flow; MIT license.
- **M2 — Pro:** budget enforcement + spend attribution + Ed25519 licensing + a
  buy link.

## Open / pre-publish tasks

- Verify the `context-guard` name is available on PyPI before publishing; pick a
  fallback (e.g. `mcp-context-guard`) if taken.
- Confirm FastMCP's proxy/aggregation API surface against the installed
  `fastmcp>=3.0` version during M1 (the plumbing assumption to validate first).
