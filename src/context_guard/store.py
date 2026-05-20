"""Local persistence for fenced raw outputs, with keyword + range retrieval.

Backed by SQLite. A normal table holds the raw blob; retrieval uses per-line
substring matching for keyword queries. Handles are short opaque ids.
The proxy process is long-lived for a session, so handles stay valid until the
size cap evicts the oldest entries.
"""
from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path


class FenceStore:
    def __init__(self, db_path: str | Path | None = None, max_bytes: int = 50_000_000):
        self.max_bytes = max_bytes
        target = ":memory:" if db_path in (None, ":memory:") else str(db_path)
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(target)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS fences ("
            "handle TEXT PRIMARY KEY, source TEXT, content TEXT, "
            "nbytes INTEGER, created REAL)"
        )
        self._db.commit()

    def put(self, content: str, *, source: str) -> str:
        handle = "h_" + secrets.token_hex(4)
        nbytes = len(content.encode("utf-8"))
        self._db.execute(
            "INSERT INTO fences VALUES (?,?,?,?,?)",
            (handle, source, content, nbytes, time.time()),
        )
        self._db.commit()
        self.prune()
        return handle

    def get(self, handle: str) -> str | None:
        row = self._db.execute(
            "SELECT content FROM fences WHERE handle=?", (handle,)
        ).fetchone()
        return row[0] if row else None

    def query(
        self,
        handle: str,
        *,
        query: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        max_chars: int = 4000,
    ) -> str:
        content = self.get(handle)
        if content is None:
            raise KeyError(handle)
        if query:
            lines = [ln for ln in content.splitlines() if query.lower() in ln.lower()]
            return "\n".join(lines)[:max_chars]
        if start_line is not None or end_line is not None:
            lines = content.splitlines()
            lo = start_line if start_line is not None else 0
            hi = (end_line + 1) if end_line is not None else len(lines)
            return "\n".join(lines[lo:hi])[:max_chars]
        return content[:max_chars]

    def prune(self) -> int:
        total = self._db.execute("SELECT COALESCE(SUM(nbytes),0) FROM fences").fetchone()[0]
        removed = 0
        while total > self.max_bytes:
            row = self._db.execute(
                "SELECT handle, nbytes FROM fences ORDER BY created ASC LIMIT 1"
            ).fetchone()
            if not row:
                break
            handle, nbytes = row
            self._db.execute("DELETE FROM fences WHERE handle=?", (handle,))
            total -= nbytes
            removed += 1
        if removed:
            self._db.commit()
        return removed

    def close(self) -> None:
        self._db.close()
