from context_guard.tokens import estimate_tokens


def test_empty_string_is_zero():
    assert estimate_tokens("") == 0


def test_longer_text_estimates_more_tokens():
    short = estimate_tokens("hello world")
    long = estimate_tokens("hello world " * 100)
    assert long > short


def test_estimate_is_positive_for_nonempty():
    assert estimate_tokens("a") >= 1


def test_roughly_quarter_of_chars_for_ascii():
    n = estimate_tokens("x" * 400)
    assert 50 <= n <= 200
