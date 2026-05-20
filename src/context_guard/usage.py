"""Per-tool token accounting for context_report()."""
from __future__ import annotations

from collections import defaultdict


class UsageTracker:
    def __init__(self) -> None:
        self._orig: dict[str, int] = defaultdict(int)
        self._ret: dict[str, int] = defaultdict(int)

    def record(self, tool: str, *, original_tokens: int, returned_tokens: int) -> None:
        self._orig[tool] += original_tokens
        self._ret[tool] += returned_tokens

    def report(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        total_o = total_r = 0
        for tool in self._orig:
            o, r = self._orig[tool], self._ret[tool]
            out[tool] = {"original": o, "returned": r, "saved": o - r}
            total_o += o
            total_r += r
        out["_total"] = {"original": total_o, "returned": total_r, "saved": total_o - total_r}
        return out
