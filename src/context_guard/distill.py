"""Deterministic, zero-API compaction of large tool outputs.

Detects JSON vs plain text and produces a compact, information-dense summary.
The full raw output is preserved in the FenceStore and retrievable via
query_fence, so distillation never loses data.
"""
from __future__ import annotations

import json
from typing import Any

_HEAD_LINES = 20
_TAIL_LINES = 10


def distill(content: str, *, max_chars: int = 1200) -> str:
    stripped = content.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            return _distill_json(json.loads(content), max_chars=max_chars)
        except (ValueError, TypeError):
            pass
    return _distill_text(content, max_chars=max_chars)


def _shape(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {_type(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        inner = _shape(value[0]) if value else "?"
        return f"[{inner}] (len={len(value)})"
    return _type(value)


def _type(value: Any) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return f"array(len={len(value)})"
    return type(value).__name__


def _distill_json(obj: Any, *, max_chars: int) -> str:
    if isinstance(obj, list):
        head = obj[:2]
        body = (
            f"JSON array, length={len(obj)}.\n"
            f"element shape: {_shape(obj[0]) if obj else '?'}\n"
            f"first items: {json.dumps(head)[:max_chars // 2]}"
        )
    else:
        body = (
            f"JSON object with keys: {list(obj.keys())}\n"
            f"shape: {_shape(obj)}"
        )
    return body[:max_chars]


def _distill_text(content: str, *, max_chars: int) -> str:
    lines = content.splitlines()
    if len(lines) <= _HEAD_LINES + _TAIL_LINES:
        return content[:max_chars]
    head = "\n".join(lines[:_HEAD_LINES])
    tail = "\n".join(lines[-_TAIL_LINES:])
    body = (
        f"text output, {len(lines)} lines, {len(content)} chars.\n"
        f"--- head ---\n{head}\n--- tail ---\n{tail}"
    )
    return body[:max_chars]
