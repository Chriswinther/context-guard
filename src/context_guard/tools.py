"""Logic for context-guard's own tools. Pure functions wrapped by server.py."""
from __future__ import annotations

import subprocess

import httpx

from context_guard.fence import fence
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker


def query_fence(
    store: FenceStore,
    handle: str,
    *,
    query: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int = 4000,
) -> str:
    try:
        return store.query(
            handle,
            query=query,
            start_line=start_line,
            end_line=end_line,
            max_chars=max_chars,
        )
    except KeyError:
        return (
            f"Handle '{handle}' not found — it may have expired (evicted by the "
            f"size cap). Re-run the original tool to regenerate it."
        )


def _fence_and_track(
    store: FenceStore, tracker: UsageTracker, content: str, *, source: str, threshold_tokens: int
) -> str:
    res = fence(content, source=source, store=store, threshold_tokens=threshold_tokens)
    tracker.record(source, original_tokens=res.original_tokens, returned_tokens=res.returned_tokens)
    return res.text


def run_fenced(
    store: FenceStore,
    tracker: UsageTracker,
    command: list[str],
    *,
    threshold_tokens: int = 2000,
    timeout: int = 120,
) -> str:
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"run_fenced: timeout after {timeout}s: {command!r}"
    output = (proc.stdout or "") + (proc.stderr or "")
    return _fence_and_track(
        store, tracker, output, source="run_fenced", threshold_tokens=threshold_tokens
    )


def fetch_fenced(
    store: FenceStore,
    tracker: UsageTracker,
    url: str,
    *,
    threshold_tokens: int = 2000,
    timeout: int = 120,
    client: httpx.Client | None = None,
) -> str:
    owns = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(url)
        body = resp.text
    except httpx.HTTPError as e:
        return f"fetch_fenced: request failed: {e}"
    finally:
        if owns:
            client.close()
    return _fence_and_track(
        store, tracker, body, source="fetch_fenced", threshold_tokens=threshold_tokens
    )


def context_report_text(tracker: UsageTracker) -> str:
    rep = tracker.report()
    lines = ["context-guard usage this session (estimated tokens):"]
    for tool, d in rep.items():
        if tool == "_total":
            continue
        lines.append(
            f"  {tool}: original={d['original']} returned={d['returned']} saved={d['saved']}"
        )
    t = rep["_total"]
    lines.append(f"  TOTAL: original={t['original']} returned={t['returned']} saved={t['saved']}")
    return "\n".join(lines)
