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


def test_query_by_keyword_windows_around_match_in_long_single_line():
    """A keyword deep inside one very long physical line (e.g. a JSON-serialized
    browser_evaluate result, where the logical newlines are escaped to '\\n')
    must be RETURNED, not truncated away. Before the fix the matching line was
    sliced [:max_chars] from its start, so a keyword sitting past max_chars was
    dropped even though the line "matched" — query_fence returned a window that
    did not contain the searched-for term. Found live-testing the Playwright MCP.
    """
    store = FenceStore(db_path=":memory:")
    filler = "x" * 8000
    content = f"HEAD {filler} NEEDLE_KW {filler} TAIL"  # one physical line, no newlines
    handle = store.put(content, source="evaluate")
    result = store.query(handle, query="NEEDLE_KW", max_chars=4000)
    assert "NEEDLE_KW" in result, "keyword window must contain the searched term"
    assert len(result) <= 4000


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


def test_put_auto_evicts_oldest_over_cap():
    store = FenceStore(db_path=":memory:", max_bytes=50)
    h1 = store.put("a" * 40, source="s")
    h2 = store.put("b" * 40, source="s")  # put() auto-prunes -> evicts h1
    assert store.get(h1) is None       # oldest auto-evicted on put
    assert store.get(h2) == "b" * 40   # newest retained


def test_prune_returns_count_removed():
    store = FenceStore(db_path=":memory:", max_bytes=10_000_000)
    store.put("a" * 40, source="s")
    store.put("b" * 40, source="s")
    store.max_bytes = 50               # shrink cap below current total
    removed = store.prune()
    assert removed >= 1
