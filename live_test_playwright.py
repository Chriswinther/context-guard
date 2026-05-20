"""Live end-to-end test: the context-guard fencing proxy in front of the REAL
Playwright MCP server (`npx @playwright/mcp@latest`).

NOT part of the pytest suite (it launches a real browser via npx and needs
network + browser binaries). Run manually to prove context-guard fences a real
third-party MCP server's oversized output, strips its output schemas, leaks no
raw payload, and serves retrieval by handle:

    .venv\\Scripts\\python.exe live_test_playwright.py

Exits 0 only if every check passes; each prints a PASS/FAIL line.

Findings this test encodes about @playwright/mcp@latest:
  * browser_navigate ALWAYS externalizes the page snapshot to a .yml file and
    returns only a file reference inline (so the inline payload is the echoed
    'Ran Playwright code' block, not the snapshot).
  * The `file:` protocol is blocked, so pages are supplied as data: URLs.
  * browser_evaluate returns its JS result INLINE as text — the realistic
    context-flooding surface, and what we use to prove fence + retrieval.
"""
from __future__ import annotations

import asyncio
import base64
import re
import sys

from fastmcp import Client

from context_guard.server import build_server
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker

# Production proxy path: an MCPConfig dict -> FastMCP.as_proxy spawns the
# downstream stdio process and context-guard aggregates + fences it.
BACKENDS = {"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}}}

# Low threshold so ordinary outputs trip the fence.
THRESHOLD = 300
OWN_TOOLS = {"query_fence", "context_report", "run_fenced", "fetch_fenced"}

# A keyword that lands well past the distiller's 1200-char head preview, so its
# presence in a fenced reply would be a genuine raw-payload leak, while its
# retrieval via query_fence proves the full output is stored.
TAIL_KEYWORD = "Link number 399"


def data_url(n_triples: int) -> str:
    body = "".join(
        f'<a href="https://example.com/{i}">Link number {i} to somewhere</a>'
        f"<button>Button {i}</button><p>Filler paragraph item {i}.</p>"
        for i in range(n_triples)
    )
    html = f"<!doctype html><html><body><h1>Fence me</h1>{body}</body></html>"
    return "data:text/html;base64," + base64.b64encode(html.encode()).decode()


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


async def main() -> int:
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    server = build_server(
        downstream_backends=BACKENDS, store=store, tracker=tracker, threshold_tokens=THRESHOLD
    )

    failures = 0
    async with Client(server) as client:
        # [1] Downstream Playwright tools surface (namespaced) alongside our own.
        tools = await client.list_tools()
        names = {t.name for t in tools}
        pw = sorted(n for n in names if "browser" in n)
        print(f"\n[1] Aggregation: {len(pw)} Playwright tools surfaced through the proxy")
        print("    e.g.", ", ".join(pw[:6]))
        failures += not check("playwright tools present", bool(pw))
        failures += not check(
            "context-guard own tools present", {"query_fence", "context_report"}.issubset(names)
        )

        # [2] Bug-A regression on a REAL downstream: a fencing proxy replaces
        #     payloads with text, so no proxied tool may still advertise an
        #     outputSchema (else strict clients reject every fenced reply).
        leaky = [
            t.name for t in tools
            if t.name not in OWN_TOOLS and getattr(t, "outputSchema", None) is not None
        ]
        print(f"\n[2] Schema strip (Bug A): {len(leaky)} proxied tools still carry outputSchema")
        failures += not check("all proxied outputSchemas stripped", not leaky, str(leaky[:5]))

        # [3] Fence a real browser_navigate (drives a real Chromium).
        nav = next(n for n in names if n.endswith("browser_navigate"))
        print(f"\n[3] {nav} on a large page (real browser)...")
        nav_res = await client.call_tool(nav, {"url": data_url(120)})
        nav_text = nav_res.content[0].text if nav_res.content else ""
        failures += not check(
            "navigate output fenced",
            "query_fence" in nav_text and bool(re.search(r"h_[0-9a-f]+", nav_text)),
            f"{len(nav_text)} chars returned",
        )

        # [4] Fence a real browser_evaluate whose JS result is large + searchable.
        ev = next(n for n in names if n.endswith("browser_evaluate"))
        fn = "() => Array.from({length: 400}, (_, i) => `Link number ${i} to somewhere`).join('\\n')"
        print(f"\n[4] {ev} returning a 400-line string (real inline payload)...")
        ev_res = await client.call_tool(ev, {"function": fn})
        ev_text = ev_res.content[0].text if ev_res.content else ""
        m = re.search(r"h_[0-9a-f]+", ev_text)
        failures += not check("evaluate output fenced", m is not None, f"{len(ev_text)} chars")

        if m:
            handle = m.group(0)
            print(f"    handle = {handle}")
            for line in ev_text.splitlines()[:2]:
                print("      |", line[:100])

            # [5] No raw leak: tail content must not survive in any caller channel.
            sc = ev_res.structured_content
            leaked = TAIL_KEYWORD in ev_text or (sc is not None and TAIL_KEYWORD in str(sc))
            failures += not check("no raw payload leak (content + structured)", not leaked)

            # [6] query_fence keyword retrieval pulls the stored tail content.
            kw = await client.call_tool("query_fence", {"handle": handle, "query": TAIL_KEYWORD})
            kw_text = kw.content[0].text if kw.content else ""
            failures += not check(
                "query_fence keyword retrieves stored payload",
                TAIL_KEYWORD in kw_text, f"{len(kw_text)} chars back",
            )

            # [7] query_fence line-range retrieval (deterministic).
            rng = await client.call_tool(
                "query_fence", {"handle": handle, "start_line": 0, "end_line": 2}
            )
            rng_text = rng.content[0].text if rng.content else ""
            failures += not check(
                "query_fence range retrieves stored payload",
                "Link number 0" in rng_text, f"{len(rng_text)} chars back",
            )

        # [8] Savings accounted across both fenced calls.
        report = await client.call_tool("context_report", {})
        rep = report.content[0].text if report.content else ""
        print("\n[8] context_report:")
        for line in rep.splitlines():
            print("    ", line)
        failures += not check("report shows savings", "saved" in rep.lower())

    print("\n" + ("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
