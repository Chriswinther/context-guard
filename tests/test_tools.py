import re
import sys

import httpx

from context_guard.store import FenceStore
from context_guard.tools import (
    context_report_text,
    fetch_fenced,
    query_fence,
    run_fenced,
)
from context_guard.usage import UsageTracker


def _readout_total(text: str) -> int | None:
    m = re.search(r"tokens saved by context-guard: ([\d,]+)", text)
    return int(m.group(1).replace(",", "")) if m else None


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


def test_run_fenced_large_output_appends_savings_readout():
    """Parity with the proxy path: a fenced native tool result must also carry the
    one-line cumulative '🛡️ tokens saved by context-guard: <N>' readout, where N
    is the running session total. (Regression: only proxied tools carried it.)"""
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store,
        tracker,
        [sys.executable, "-c", "print('x' * 40000)"],
        threshold_tokens=2000,
    )
    total = _readout_total(out)
    assert total is not None and total > 0
    assert total == tracker.report()["_total"]["saved"]


def test_run_fenced_small_output_has_no_readout():
    """Small pass-through results stay clean — no readout, matching the proxy path."""
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store, tracker, [sys.executable, "-c", "print('hello')"], threshold_tokens=2000
    )
    assert _readout_total(out) is None


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


def test_run_fenced_resolves_bare_python_interpreter():
    """Bug B: a bare 'python' command must run the interpreter, not hang.

    On Windows bare 'python' resolves to the Microsoft Store app-execution-alias
    stub which blocks forever in a non-interactive subprocess. run_fenced must
    map it to the interpreter actually running context-guard (sys.executable).
    """
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store, tracker, ["python", "-c", "print('viapython')"],
        threshold_tokens=2000, timeout=30,
    )
    assert "viapython" in out


def test_run_fenced_unknown_command_returns_friendly_error():
    """Bug B: an unresolvable command must return a friendly message, not raise
    a FileNotFoundError/WinError 2 out of the tool."""
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store, tracker, ["definitely_not_a_real_command_xyz123"],
        threshold_tokens=2000, timeout=30,
    )
    assert "not found" in out.lower()


def test_run_fenced_finds_pip_via_interpreter_scripts():
    """Bug B regression for the exact live failure: bare 'pip' was 'not
    recognized' because the interpreter's Scripts dir was not on the subprocess
    PATH. run_fenced augments PATH with the interpreter dir + Scripts."""
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store, tracker, ["pip", "--version"], threshold_tokens=2000, timeout=60
    )
    assert "pip" in out.lower()


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


def test_fetch_fenced_large_output_appends_savings_readout():
    """fetch_fenced, like run_fenced, must carry the cumulative savings readout
    when it fences (parity with the proxy middleware path)."""
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="y" * 40000))
    client = httpx.Client(transport=transport)
    out = fetch_fenced(
        store, tracker, "https://example.com/big", threshold_tokens=2000, client=client
    )
    total = _readout_total(out)
    assert total is not None and total > 0
    assert total == tracker.report()["_total"]["saved"]


def test_context_report_text_summarizes_savings():
    tracker = UsageTracker()
    tracker.record("github_search", original_tokens=1000, returned_tokens=100)
    text = context_report_text(tracker)
    assert "github_search" in text
    assert "900" in text
