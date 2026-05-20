import pytest

from context_guard.store import FenceStore


def test_put_returns_handle_and_get_roundtrips():
    store = FenceStore(db_path=":memory:")
    handle = store.put("the quick brown fox", source="github_search")
    assert handle.startswith("h_")
    assert store.get(handle) == "the quick brown fox"


def test_get_unknown_handle_returns_none():
    store = FenceStore(db_path=":memory:")
    assert store.get("h_doesnotexist") is None


def test_query_unknown_handle_raises_keyerror():
    store = FenceStore(db_path=":memory:")
    with pytest.raises(KeyError):
        store.query("h_nope", query="anything")


def test_query_by_keyword_returns_matching_lines():
    store = FenceStore(db_path=":memory:")
    content = "line about apples\nline about bananas\nline about cherries"
    handle = store.put(content, source="logs")
    result = store.query(handle, query="bananas")
    assert "bananas" in result
    assert "apples" not in result


def test_query_by_line_range():
    store = FenceStore(db_path=":memory:")
    content = "\n".join(f"row{i}" for i in range(10))
    handle = store.put(content, source="logs")
    result = store.query(handle, start_line=2, end_line=4)
    assert "row2" in result and "row4" in result
    assert "row0" not in result and "row5" not in result


def test_query_default_returns_head_when_no_args():
    store = FenceStore(db_path=":memory:")
    content = "\n".join(f"row{i}" for i in range(100))
    handle = store.put(content, source="logs")
    result = store.query(handle, max_chars=40)
    assert "row0" in result
    assert len(result) <= 40


def test_prune_evicts_oldest_over_cap():
    store = FenceStore(db_path=":memory:", max_bytes=50)
    h1 = store.put("a" * 40, source="s")
    store.put("b" * 40, source="s")
    removed = store.prune()
    assert removed >= 1
    assert store.get(h1) is None
