"""The fence decision: pass small outputs through; for large ones, store the raw
output and return a compact distillation + retrieval handle instead.
"""
from __future__ import annotations

from dataclasses import dataclass

from context_guard.distill import distill
from context_guard.store import FenceStore
from context_guard.tokens import estimate_tokens


@dataclass
class FenceResult:
    fenced: bool
    text: str
    handle: str | None
    original_tokens: int
    returned_tokens: int


def fence(
    content: str,
    *,
    source: str,
    store: FenceStore,
    threshold_tokens: int = 2000,
) -> FenceResult:
    original_tokens = estimate_tokens(content)
    if original_tokens <= threshold_tokens:
        return FenceResult(
            fenced=False,
            text=content,
            handle=None,
            original_tokens=original_tokens,
            returned_tokens=original_tokens,
        )
    handle = store.put(content, source=source)
    summary = distill(content)
    text = (
        f"[context-guard fenced {original_tokens} est. tokens from '{source}']\n"
        f"{summary}\n"
        f"Full output stored as handle '{handle}'. "
        f"Call query_fence(handle='{handle}', query='<keyword>') "
        f"or query_fence(handle='{handle}', start_line=N, end_line=M) to retrieve specifics."
    )
    return FenceResult(
        fenced=True,
        text=text,
        handle=handle,
        original_tokens=original_tokens,
        returned_tokens=estimate_tokens(text),
    )
