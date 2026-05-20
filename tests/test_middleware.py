import pytest

from context_guard.middleware import extract_text, fence_payload
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker


def test_extract_text_joins_text_blocks():
    class Block:
        def __init__(self, text):
            self.type = "text"
            self.text = text

    blocks = [Block("hello "), Block("world")]
    assert extract_text(blocks) == "hello world"


def test_extract_text_handles_plain_string():
    assert extract_text("just a string") == "just a string"


def test_fence_payload_passes_small_through():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out, fenced = fence_payload(
        "small", tool_name="github_search", store=store, tracker=tracker, threshold_tokens=2000
    )
    assert fenced is False
    assert out == "small"


def test_fence_payload_fences_large_and_records():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out, fenced = fence_payload(
        "x" * 40000,
        tool_name="github_search",
        store=store,
        tracker=tracker,
        threshold_tokens=2000,
    )
    assert fenced is True
    assert "query_fence" in out
    assert tracker.report()["github_search"]["saved"] > 0
