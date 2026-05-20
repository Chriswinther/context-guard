import json

from context_guard.distill import distill


def test_distill_json_object_reports_shape():
    payload = json.dumps({"items": list(range(500)), "total": 500, "page": 1})
    out = distill(payload)
    assert "items" in out and "total" in out
    assert "500" in out
    assert len(out) < len(payload)


def test_distill_json_array_reports_length_and_samples():
    payload = json.dumps([{"id": i} for i in range(300)])
    out = distill(payload)
    assert "300" in out
    assert "id" in out


def test_distill_plain_text_keeps_head_and_tail():
    text = "\n".join(f"log line {i}" for i in range(200))
    out = distill(text)
    assert "log line 0" in out
    assert "log line 199" in out
    assert "200" in out
    assert len(out) < len(text)


def test_distill_respects_max_chars_budget():
    out = distill("z" * 100000, max_chars=500)
    assert len(out) <= 700
