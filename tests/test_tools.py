import sys

import httpx

from context_guard.store import FenceStore
from context_guard.usage import UsageTracker
from context_guard.tools import (
    context_report_text,
    fetch_fenced,
    query_fence,
    run_fenced,
)


def test_query_fence_returns_slice():
    store = FenceStore(db_path=":memory:")
    handle = store.put("alpha\nbeta\ngamma", source="s")
    assert "beta" in query_fence(store, handle, query="beta")


def test_query_fence_unknown_handle_message():
    store = FenceStore(db_path=":memory:")
    out = query_fence(store, "h_missing", query="x")
    assert "expired" in out.lower() or "re-run" in out.lower()


def test_run_fenced_small_output_passthrough():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store, tracker, [sys.executable, "-c", "print('hello')"], threshold_tokens=2000
    )
    assert "hello" in out


def test_run_fenced_large_output_is_fenced():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store,
        tracker,
        [sys.executable, "-c", "print('x' * 40000)"],
        threshold_tokens=2000,
    )
    assert "query_fence" in out
    assert tracker.report()["_total"]["saved"] > 0


def test_run_fenced_timeout_returns_error_not_hang():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store,
        tracker,
        [sys.executable, "-c", "import time; time.sleep(5)"],
        threshold_tokens=2000,
        timeout=1,
    )
    assert "timeout" in out.lower()


def test_fetch_fenced_uses_injected_client_and_fences_large():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text="y" * 40000)
    )
    client = httpx.Client(transport=transport)
    out = fetch_fenced(
        store, tracker, "https://example.com/big", threshold_tokens=2000, client=client
    )
    assert "query_fence" in out


def test_context_report_text_summarizes_savings():
    tracker = UsageTracker()
    tracker.record("github_search", original_tokens=1000, returned_tokens=100)
    text = context_report_text(tracker)
    assert "github_search" in text
    assert "900" in text
