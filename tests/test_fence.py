from context_guard.fence import FenceResult, fence
from context_guard.store import FenceStore


def test_small_output_passes_through_unfenced():
    store = FenceStore(db_path=":memory:")
    res = fence("tiny output", source="t", store=store, threshold_tokens=2000)
    assert isinstance(res, FenceResult)
    assert res.fenced is False
    assert res.text == "tiny output"
    assert res.handle is None


def test_large_output_is_fenced_with_handle_and_hint():
    store = FenceStore(db_path=":memory:")
    big = "x" * 40000
    res = fence(big, source="github_search", store=store, threshold_tokens=2000)
    assert res.fenced is True
    assert res.handle is not None
    assert "query_fence" in res.text
    assert res.handle in res.text
    assert res.returned_tokens < res.original_tokens
    assert store.get(res.handle) == big


def test_threshold_boundary_passes_through_when_at_or_below():
    store = FenceStore(db_path=":memory:")
    res = fence("abcd", source="t", store=store, threshold_tokens=2000)
    assert res.fenced is False
