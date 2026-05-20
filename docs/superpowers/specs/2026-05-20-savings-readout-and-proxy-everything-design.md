# Design: savings readout on every fenced call + proxy every MCP server

Date: 2026-05-20
Status: approved (user), implementing

## Goal
1. context-guard appends a one-line **cumulative** savings readout to every call it
   actually saved tokens on: `🛡️ tokens saved by context-guard: <N>`.
2. context-guard sits in front of **every** MCP server the user calls (filesystem,
   Playwright, sqlite, github) across the global scope and the Trade project, so the
   fence (and the readout) fires on every MCP call.

## Part 1 — Readout (code: `src/context_guard/middleware.py`)
- In `FenceMiddleware.on_call_tool`, when `fenced` is True, append a trailing line to
  the fenced text: `\n\n🛡️ tokens saved by context-guard: {total:,}` where
  `total = tracker.report()["_total"]["saved"]`.
- Applied to both `result.content` and `result.structured_content`.
- Only on fenced calls (small / pass-through calls get nothing).
- Tests: line present + correct cumulative total on a fenced call; absent on a
  non-fenced call; total accumulates across multiple fenced calls.

## Part 2a — HTTP downstream support (code: `config.py`, `server.py`)
The github MCP is `type: http` (remote url + Bearer header). context-guard's config
currently only models stdio `command`/`args`/`env`. Extend so a server spec may
instead carry `url` (+ optional `headers`, `transport`):
- `ServerSpec` gains optional `url`, `headers`, `transport`.
- `load_config` accepts either a `command` spec or a `url` spec (error if neither).
- `_backends_from_config` emits `{"command","args","env"}` for stdio specs and
  `{"url"/"transport","headers"}` for http specs (FastMCP MCPConfig remote form).
- Tests: loader parses an http server; `_backends_from_config` shape is correct.

## Part 2b — Config files
- Global `context-guard.toml`: `[servers.filesystem]` (SideHusstle root) + `[servers.playwright]`.
- Trade `context-guard.trade.toml`: `sqlite` (trade.db), `filesystem` (Trade root),
  `playwright`, `github` (http url + Authorization header).
- Threshold: keep the production default (2000) in both.

## Part 2c — `.claude.json` rewiring (live edit, backed up first)
- Back up `.claude.json` → `.claude.json.bak-<ts>`.
- Global `mcpServers.context-guard.env.CONTEXT_GUARD_CONFIG` → new global `context-guard.toml`.
- Trade project `mcpServers`: remove `sqlite`/`filesystem`/`playwright`/`github`; add a
  single `context-guard` stdio entry (same exe) with `CONTEXT_GUARD_CONFIG` → `context-guard.trade.toml`.
- Disable the Playwright **plugin** (so Playwright flows only through context-guard, no
  duplicate `mcp__plugin_playwright_*` tools). Investigate the actual toggle location;
  if not cleanly editable, document the manual step.

## Constraints / risks
- **Restart required**: MCP/plugin changes load at Claude Code startup; nothing appears
  this session.
- Tool names become `mcp__context-guard__<server>_<tool>`.
- Wrapping `github` (remote HTTP) adds a startup dependency; it is the one fragile
  backend and can be dropped if startup gets flaky.
- github PAT remains plaintext (moved from `.claude.json` to the toml, same exposure).
- Editing `.claude.json` while Claude Code runs risks being overwritten; back up and
  restart promptly.

## Verification
- Phase A: `pytest -q` green (incl. new tests), ruff clean; re-run the live Playwright
  harness to confirm the readout line appears through the real proxy.
- Phase B: `load_config` parses both new toml files and `build_server` constructs a
  proxy from each (smoke), without requiring a full client connect.
- Then user restarts Claude Code.
