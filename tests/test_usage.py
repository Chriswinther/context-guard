from context_guard.usage import UsageTracker


def test_records_and_reports_per_tool_totals():
    t = UsageTracker()
    t.record("github_search", original_tokens=1000, returned_tokens=200)
    t.record("github_search", original_tokens=500, returned_tokens=100)
    t.record("playwright_snapshot", original_tokens=300, returned_tokens=300)
    rep = t.report()
    assert rep["github_search"]["original"] == 1500
    assert rep["github_search"]["returned"] == 300
    assert rep["github_search"]["saved"] == 1200
    assert rep["playwright_snapshot"]["saved"] == 0


def test_report_includes_totals_row():
    t = UsageTracker()
    t.record("a", original_tokens=100, returned_tokens=10)
    rep = t.report()
    assert rep["_total"]["original"] == 100
    assert rep["_total"]["saved"] == 90


def test_empty_report():
    assert UsageTracker().report() == {"_total": {"original": 0, "returned": 0, "saved": 0}}
