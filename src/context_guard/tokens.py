"""Token-count estimation.

Anthropic's exact tokenizer is not public, so this is an ESTIMATE used only for
fence thresholds and reporting. Uses tiktoken's cl100k encoding if installed
(closer to real BPE), otherwise a chars/4 heuristic.
"""
from __future__ import annotations

_CHARS_PER_TOKEN = 4

try:  # optional dependency
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - exercised only when tiktoken absent
    _ENC = None


def estimate_tokens(text: str) -> int:
    """Estimate the number of tokens in ``text``. Always >= 0."""
    if not text:
        return 0
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, len(text) // _CHARS_PER_TOKEN)
