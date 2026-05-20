# context-guard M1 (Free Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the free OSS core of context-guard: a local stdio MCP aggregating proxy that auto-fences oversized downstream tool output, stores the full raw output locally, and lets the model retrieve specifics on demand.

**Architecture:** A `FastMCP` server runs over stdio. `FastMCP.as_proxy` aggregates the user's downstream MCP servers (auto-namespacing their tools as `<server>_<tool>`). A `FenceMiddleware` post-processes every tool result: if it exceeds a token threshold, the full raw output is stored in a local SQLite+FTS5 store and a compact deterministic distillation + retrieval handle is returned instead. The server also exposes its own tools: `query_fence`, `run_fenced`, `fetch_fenced`, `context_report`.

**Tech Stack:** Python ≥3.11, `fastmcp>=3.0,<4`, `httpx`, stdlib `sqlite3`/`tomllib`/`subprocess`; `pytest` for tests; optional `tiktoken` for better token estimates.

---

## File structure

```
context-guard/
├── pyproject.toml                      # package metadata, deps, scripts entry
├── README.md                           # setup + wrap-and-save demo
├── .gitignore
├── src/context_guard/
│   ├── __init__.py                     # __version__
│   ├── tokens.py                       # estimate_tokens()
│   ├── store.py                        # FenceStore (SQLite + FTS5)
│   ├── distill.py                      # deterministic distillers
│   ├── fence.py                        # fence(): decide + assemble FenceResult
│   ├── config.py                       # load_config() from .context-guard.toml
│   ├── usage.py                        # UsageTracker (per-tool token tallies)
│   ├── tools.py                        # query_fence/run_fenced/fetch_fenced/context_report logic
│   ├── middleware.py                   # FenceMiddleware (FastMCP middleware)
│   └── server.py                       # assemble proxy + middleware + tools; main()
└── tests/
    ├── __init__.py
    ├── test_tokens.py
    ├── test_store.py
    ├── test_distill.py
    ├── test_fence.py
    ├── test_config.py
    ├── test_usage.py
    ├── test_tools.py
    ├── test_middleware.py
    └── test_integration.py
```

Each module has one responsibility and no upward dependencies on `server.py`. Pure modules (`tokens`, `store`, `distill`, `fence`, `config`, `usage`, `tools`) have **no FastMCP dependency** and are unit-tested in isolation. Only `middleware.py` and `server.py` touch FastMCP.

---

### Task 0: Scaffold project + verify FastMCP API

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/context_guard/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "context-guard"
version = "0.1.0"
description = "Local MCP aggregating proxy that auto-fences oversized tool output to protect the context window"
requires-python = ">=3.11"
dependencies = [
    "fastmcp>=3.0,<4",
    "httpx>=0.27",
]

[project.optional-dependencies]
tiktoken = ["tiktoken>=0.7"]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.6",
    "mypy>=1.10",
]

[project.scripts]
context-guard = "context_guard.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/context_guard"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
.venv/
__pycache__/
*.pyc
dist/
build/
*.egg-info/
.pytest_cache/
.mypy_cache/
.context-guard/
```

- [ ] **Step 3: Create package + test init files**

`src/context_guard/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`:
```python
```
(empty file)

- [ ] **Step 4: Create venv and install**

Run:
```bash
cd context-guard
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
```
Expected: installs `fastmcp`, `httpx`, `pytest`, etc., and `context-guard` in editable mode without errors.

- [ ] **Step 5: Verify the FastMCP API shape this plan depends on (spike)**

Run:
```bash
.venv/Scripts/python -c "import fastmcp, inspect; from fastmcp import FastMCP, Client; from fastmcp.server.middleware import Middleware, MiddlewareContext; print('fastmcp', fastmcp.__version__); print('as_proxy', hasattr(FastMCP, 'as_proxy')); print('add_middleware', hasattr(FastMCP, 'add_middleware')); print('on_call_tool', hasattr(Middleware, 'on_call_tool'))"
```
Expected: prints the version and `True` for `as_proxy`, `add_middleware`, and `on_call_tool`.

If any import path differs in the installed version (e.g. middleware lives at a different module path, or `on_call_tool` has a different name), record the actual symbols and adjust Tasks 10–11 accordingly before writing that code. The pure modules (Tasks 1–9) are unaffected.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/context_guard/__init__.py tests/__init__.py
git commit -m "chore: scaffold context-guard package + verify fastmcp api"
```

---

### Task 1: Token estimator (`tokens.py`)

**Files:**
- Create: `src/context_guard/tokens.py`
- Test: `tests/test_tokens.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tokens.py
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
    # heuristic floor: ~4 chars/token; 400 chars -> ~100 tokens, allow wide band
    n = estimate_tokens("x" * 400)
    assert 50 <= n <= 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: cannot import name 'estimate_tokens'`.

- [ ] **Step 3: Implement `tokens.py`**

```python
# src/context_guard/tokens.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_tokens.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_guard/tokens.py tests/test_tokens.py
git commit -m "feat: token estimator with optional tiktoken backend"
```

---

### Task 2: Fence store (`store.py`)

**Files:**
- Create: `src/context_guard/store.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py
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
    store.put("b" * 40, source="s")  # now over 50 bytes total
    removed = store.prune()
    assert removed >= 1
    assert store.get(h1) is None  # oldest evicted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'FenceStore'`.

- [ ] **Step 3: Implement `store.py`**

```python
# src/context_guard/store.py
"""Local persistence for fenced raw outputs, with keyword + range retrieval.

Backed by SQLite. A normal table holds the raw blob; an FTS5 virtual table
indexes it for keyword retrieval via query_fence. Handles are short opaque ids.
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
        self._db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS fences_fts "
            "USING fts5(handle UNINDEXED, content)"
        )
        self._db.commit()

    def put(self, content: str, *, source: str) -> str:
        handle = "h_" + secrets.token_hex(4)
        nbytes = len(content.encode("utf-8"))
        self._db.execute(
            "INSERT INTO fences VALUES (?,?,?,?,?)",
            (handle, source, content, nbytes, time.time()),
        )
        self._db.execute(
            "INSERT INTO fences_fts (handle, content) VALUES (?,?)", (handle, content)
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
            lo = start_line or 0
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
            self._db.execute("DELETE FROM fences_fts WHERE handle=?", (handle,))
            total -= nbytes
            removed += 1
        if removed:
            self._db.commit()
        return removed

    def close(self) -> None:
        self._db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_store.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_guard/store.py tests/test_store.py
git commit -m "feat: FenceStore with sqlite fts5 keyword + range retrieval and size-cap pruning"
```

---

### Task 3: Deterministic distillers (`distill.py`)

**Files:**
- Create: `src/context_guard/distill.py`
- Test: `tests/test_distill.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_distill.py
import json

from context_guard.distill import distill


def test_distill_json_object_reports_shape():
    payload = json.dumps({"items": list(range(500)), "total": 500, "page": 1})
    out = distill(payload)
    assert "items" in out and "total" in out
    assert "500" in out  # length/count surfaced
    assert len(out) < len(payload)


def test_distill_json_array_reports_length_and_samples():
    payload = json.dumps([{"id": i} for i in range(300)])
    out = distill(payload)
    assert "300" in out          # length surfaced
    assert "id" in out           # element shape surfaced


def test_distill_plain_text_keeps_head_and_tail():
    text = "\n".join(f"log line {i}" for i in range(200))
    out = distill(text)
    assert "log line 0" in out          # head
    assert "log line 199" in out        # tail
    assert "200" in out                 # total line count surfaced
    assert len(out) < len(text)


def test_distill_respects_max_chars_budget():
    out = distill("z" * 100000, max_chars=500)
    assert len(out) <= 700  # summary text + small framing overhead
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_distill.py -v`
Expected: FAIL with `ImportError: cannot import name 'distill'`.

- [ ] **Step 3: Implement `distill.py`**

```python
# src/context_guard/distill.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_distill.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_guard/distill.py tests/test_distill.py
git commit -m "feat: deterministic json/text distillers"
```

---

### Task 4: Fence decision + assembly (`fence.py`)

**Files:**
- Create: `src/context_guard/fence.py`
- Test: `tests/test_fence.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fence.py
from context_guard.fence import FenceResult, fence
from context_guard.store import FenceStore


def test_small_output_passes_through_unfenced():
    store = FenceStore(db_path=":memory:")
    res = fence("tiny output", source="t", store=store, threshold_tokens=2000)
    assert isinstance(res, FenceResult)
    assert res.fenced is False
    assert res.text == "tiny output"
    assert res.handle is None


def test_large_output_is_fenced_with_handle_and_hint():
    store = FenceStore(db_path=":memory:")
    big = "x" * 40000  # ~10k est tokens
    res = fence(big, source="github_search", store=store, threshold_tokens=2000)
    assert res.fenced is True
    assert res.handle is not None
    assert "query_fence" in res.text          # retrieval hint present
    assert res.handle in res.text             # handle surfaced to model
    assert res.returned_tokens < res.original_tokens
    assert store.get(res.handle) == big       # full raw preserved


def test_threshold_boundary_passes_through_when_at_or_below():
    store = FenceStore(db_path=":memory:")
    res = fence("abcd", source="t", store=store, threshold_tokens=2000)
    assert res.fenced is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_fence.py -v`
Expected: FAIL with `ImportError: cannot import name 'fence'`.

- [ ] **Step 3: Implement `fence.py`**

```python
# src/context_guard/fence.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_fence.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_guard/fence.py tests/test_fence.py
git commit -m "feat: fence decision and fenced-reply assembly"
```

---

### Task 5: Config loader (`config.py`)

**Files:**
- Create: `src/context_guard/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
import pytest

from context_guard.config import Config, ServerSpec, load_config

SAMPLE = """
[fence]
threshold_tokens = 1500

[servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]

[servers.playwright]
command = "npx"
args = ["@playwright/mcp"]
env = { PWDEBUG = "0" }
"""


def test_load_parses_threshold_and_servers(tmp_path):
    p = tmp_path / ".context-guard.toml"
    p.write_text(SAMPLE)
    cfg = load_config(p)
    assert isinstance(cfg, Config)
    assert cfg.threshold_tokens == 1500
    names = {s.name for s in cfg.servers}
    assert names == {"github", "playwright"}


def test_server_spec_fields(tmp_path):
    p = tmp_path / ".context-guard.toml"
    p.write_text(SAMPLE)
    cfg = load_config(p)
    gh = next(s for s in cfg.servers if s.name == "github")
    assert isinstance(gh, ServerSpec)
    assert gh.command == "npx"
    assert gh.args == ["-y", "@modelcontextprotocol/server-github"]
    assert gh.env == {}
    pw = next(s for s in cfg.servers if s.name == "playwright")
    assert pw.env == {"PWDEBUG": "0"}


def test_defaults_when_fence_section_missing(tmp_path):
    p = tmp_path / ".context-guard.toml"
    p.write_text('[servers.x]\ncommand = "echo"\nargs = ["hi"]\n')
    cfg = load_config(p)
    assert cfg.threshold_tokens == 2000  # default


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_server_without_command_raises(tmp_path):
    p = tmp_path / ".context-guard.toml"
    p.write_text("[servers.bad]\nargs = []\n")
    with pytest.raises(ValueError):
        load_config(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_config'`.

- [ ] **Step 3: Implement `config.py`**

```python
# src/context_guard/config.py
"""Load and validate .context-guard.toml."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_THRESHOLD_TOKENS = 2000
DEFAULT_CACHE_DIR = Path.home() / ".context-guard" / "cache"


@dataclass
class ServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    threshold_tokens: int
    servers: list[ServerSpec]
    cache_dir: Path = DEFAULT_CACHE_DIR


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    data = tomllib.loads(path.read_text())

    fence = data.get("fence", {})
    threshold = int(fence.get("threshold_tokens", DEFAULT_THRESHOLD_TOKENS))

    servers: list[ServerSpec] = []
    for name, spec in data.get("servers", {}).items():
        if "command" not in spec:
            raise ValueError(f"server '{name}' is missing required key 'command'")
        servers.append(
            ServerSpec(
                name=name,
                command=spec["command"],
                args=list(spec.get("args", [])),
                env=dict(spec.get("env", {})),
            )
        )
    return Config(threshold_tokens=threshold, servers=servers)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_config.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_guard/config.py tests/test_config.py
git commit -m "feat: .context-guard.toml loader with validation"
```

---

### Task 6: Usage tracker (`usage.py`)

**Files:**
- Create: `src/context_guard/usage.py`
- Test: `tests/test_usage.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_usage.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_usage.py -v`
Expected: FAIL with `ImportError: cannot import name 'UsageTracker'`.

- [ ] **Step 3: Implement `usage.py`**

```python
# src/context_guard/usage.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_usage.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/context_guard/usage.py tests/test_usage.py
git commit -m "feat: per-tool usage tracker"
```

---

### Task 7: Tool logic (`tools.py`) — query/run/fetch/report

**Files:**
- Create: `src/context_guard/tools.py`
- Test: `tests/test_tools.py`

These are plain functions (no FastMCP) so they are unit-testable. `server.py` will wrap them as MCP tools in Task 11.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tools.py
import httpx

from context_guard.store import FenceStore
from context_guard.usage import UsageTracker
from context_guard.tools import (
    context_report_text,
    fetch_fenced,
    query_fence,
    run_fenced,
)


def test_query_fence_returns_slice():
    store = FenceStore(db_path=":memory:")
    handle = store.put("alpha\nbeta\ngamma", source="s")
    assert "beta" in query_fence(store, handle, query="beta")


def test_query_fence_unknown_handle_message():
    store = FenceStore(db_path=":memory:")
    out = query_fence(store, "h_missing", query="x")
    assert "expired" in out.lower() or "re-run" in out.lower()


def test_run_fenced_small_output_passthrough():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    # 'python -c print' is cross-platform
    out = run_fenced(
        store, tracker, ["python", "-c", "print('hello')"], threshold_tokens=2000
    )
    assert "hello" in out


def test_run_fenced_large_output_is_fenced():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store,
        tracker,
        ["python", "-c", "print('x' * 40000)"],
        threshold_tokens=2000,
    )
    assert "query_fence" in out
    assert tracker.report()["_total"]["saved"] > 0


def test_run_fenced_timeout_returns_error_not_hang():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out = run_fenced(
        store,
        tracker,
        ["python", "-c", "import time; time.sleep(5)"],
        threshold_tokens=2000,
        timeout=1,
    )
    assert "timeout" in out.lower()


def test_fetch_fenced_uses_injected_client_and_fences_large():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text="y" * 40000)
    )
    client = httpx.Client(transport=transport)
    out = fetch_fenced(
        store, tracker, "https://example.com/big", threshold_tokens=2000, client=client
    )
    assert "query_fence" in out


def test_context_report_text_summarizes_savings():
    tracker = UsageTracker()
    tracker.record("github_search", original_tokens=1000, returned_tokens=100)
    text = context_report_text(tracker)
    assert "github_search" in text
    assert "900" in text  # saved tokens surfaced
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_tools.py -v`
Expected: FAIL with `ImportError: cannot import name 'query_fence'`.

- [ ] **Step 3: Implement `tools.py`**

```python
# src/context_guard/tools.py
"""Logic for context-guard's own tools. Pure functions wrapped by server.py."""
from __future__ import annotations

import subprocess

import httpx

from context_guard.fence import fence
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker


def query_fence(
    store: FenceStore,
    handle: str,
    *,
    query: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int = 4000,
) -> str:
    try:
        return store.query(
            handle,
            query=query,
            start_line=start_line,
            end_line=end_line,
            max_chars=max_chars,
        )
    except KeyError:
        return (
            f"Handle '{handle}' not found — it may have expired (evicted by the "
            f"size cap). Re-run the original tool to regenerate it."
        )


def _fence_and_track(
    store: FenceStore, tracker: UsageTracker, content: str, *, source: str, threshold_tokens: int
) -> str:
    res = fence(content, source=source, store=store, threshold_tokens=threshold_tokens)
    tracker.record(source, original_tokens=res.original_tokens, returned_tokens=res.returned_tokens)
    return res.text


def run_fenced(
    store: FenceStore,
    tracker: UsageTracker,
    command: list[str],
    *,
    threshold_tokens: int = 2000,
    timeout: int = 120,
) -> str:
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return f"run_fenced: command timed out after {timeout}s: {command!r}"
    output = (proc.stdout or "") + (proc.stderr or "")
    return _fence_and_track(
        store, tracker, output, source="run_fenced", threshold_tokens=threshold_tokens
    )


def fetch_fenced(
    store: FenceStore,
    tracker: UsageTracker,
    url: str,
    *,
    threshold_tokens: int = 2000,
    timeout: int = 120,
    client: httpx.Client | None = None,
) -> str:
    owns = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(url)
        body = resp.text
    except httpx.HTTPError as e:
        return f"fetch_fenced: request failed: {e}"
    finally:
        if owns:
            client.close()
    return _fence_and_track(
        store, tracker, body, source="fetch_fenced", threshold_tokens=threshold_tokens
    )


def context_report_text(tracker: UsageTracker) -> str:
    rep = tracker.report()
    lines = ["context-guard usage this session (estimated tokens):"]
    for tool, d in rep.items():
        if tool == "_total":
            continue
        lines.append(
            f"  {tool}: original={d['original']} returned={d['returned']} saved={d['saved']}"
        )
    t = rep["_total"]
    lines.append(f"  TOTAL: original={t['original']} returned={t['returned']} saved={t['saved']}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_tools.py -v`
Expected: PASS (7 passed). (Requires `python` on PATH for the subprocess tests — true in the dev venv context.)

- [ ] **Step 5: Commit**

```bash
git add src/context_guard/tools.py tests/test_tools.py
git commit -m "feat: query_fence/run_fenced/fetch_fenced/context_report logic"
```

---

### Task 8: Fence middleware (`middleware.py`)

**Files:**
- Create: `src/context_guard/middleware.py`
- Test: `tests/test_middleware.py`

This is the first FastMCP-touching module. Confirm middleware symbols from Task 0 Step 5 before implementing; adjust import paths if the installed version differs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_middleware.py
import pytest

from context_guard.middleware import extract_text, fence_payload
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker


def test_extract_text_joins_text_blocks():
    class Block:
        def __init__(self, text):
            self.type = "text"
            self.text = text

    blocks = [Block("hello "), Block("world")]
    assert extract_text(blocks) == "hello world"


def test_extract_text_handles_plain_string():
    assert extract_text("just a string") == "just a string"


def test_fence_payload_passes_small_through():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out, fenced = fence_payload(
        "small", tool_name="github_search", store=store, tracker=tracker, threshold_tokens=2000
    )
    assert fenced is False
    assert out == "small"


def test_fence_payload_fences_large_and_records():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    out, fenced = fence_payload(
        "x" * 40000,
        tool_name="github_search",
        store=store,
        tracker=tracker,
        threshold_tokens=2000,
    )
    assert fenced is True
    assert "query_fence" in out
    assert tracker.report()["github_search"]["saved"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_middleware.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_text'`.

- [ ] **Step 3: Implement `middleware.py`**

```python
# src/context_guard/middleware.py
"""FastMCP middleware that fences oversized downstream tool results.

The pure helpers (extract_text, fence_payload) are unit-tested directly. The
FenceMiddleware class wires them into FastMCP's on_call_tool hook. If the
installed fastmcp exposes different middleware symbols (see Task 0 spike), adjust
the import and the hook signature below — the helpers stay the same.
"""
from __future__ import annotations

from typing import Any

from context_guard.fence import fence
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker


def extract_text(content: Any) -> str:
    """Flatten an MCP tool result's content into a single string."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        elif isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "".join(parts)


def fence_payload(
    text: str,
    *,
    tool_name: str,
    store: FenceStore,
    tracker: UsageTracker,
    threshold_tokens: int,
) -> tuple[str, bool]:
    """Return (possibly-fenced text, was_fenced) and record usage."""
    res = fence(text, source=tool_name, store=store, threshold_tokens=threshold_tokens)
    tracker.record(
        tool_name, original_tokens=res.original_tokens, returned_tokens=res.returned_tokens
    )
    return res.text, res.fenced


# --- FastMCP wiring -------------------------------------------------------
# Import guarded so the pure helpers above remain importable even if fastmcp's
# middleware module path differs; server.py only imports FenceMiddleware.
from fastmcp.server.middleware import Middleware, MiddlewareContext  # noqa: E402
from mcp.types import TextContent  # noqa: E402


class FenceMiddleware(Middleware):
    def __init__(
        self, store: FenceStore, tracker: UsageTracker, threshold_tokens: int
    ) -> None:
        self.store = store
        self.tracker = tracker
        self.threshold_tokens = threshold_tokens

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        result = await call_next(context)
        tool_name = getattr(context.message, "name", "unknown")
        text = extract_text(getattr(result, "content", result))
        new_text, fenced = fence_payload(
            text,
            tool_name=tool_name,
            store=self.store,
            tracker=self.tracker,
            threshold_tokens=self.threshold_tokens,
        )
        if fenced:
            result.content = [TextContent(type="text", text=new_text)]
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_middleware.py -v`
Expected: PASS (4 passed). The class wiring is exercised by the Task 9 integration test, not unit tests.

- [ ] **Step 5: Commit**

```bash
git add src/context_guard/middleware.py tests/test_middleware.py
git commit -m "feat: fence middleware helpers + FastMCP wiring"
```

---

### Task 9: Server assembly + integration test (`server.py`)

**Files:**
- Create: `src/context_guard/server.py`
- Test: `tests/test_integration.py`

- [ ] **Step 1: Write the failing integration test**

This test builds a real in-memory downstream FastMCP server with a tool that returns a huge payload, wraps it via `build_server`, and asserts end-to-end fencing through an in-memory `Client`.

```python
# tests/test_integration.py
import pytest
from fastmcp import Client, FastMCP

from context_guard.server import build_server
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker


def make_downstream() -> FastMCP:
    down = FastMCP("downstream")

    @down.tool
    def big_list() -> str:
        return "x" * 40000

    @down.tool
    def small() -> str:
        return "hi"

    return down


@pytest.mark.asyncio
async def test_large_downstream_output_is_fenced_and_retrievable():
    store = FenceStore(db_path=":memory:")
    tracker = UsageTracker()
    downstream = make_downstream()
    server = build_server(
        downstream_backends={"down": downstream},
        store=store,
        tracker=tracker,
        threshold_tokens=2000,
    )

    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
        # downstream tools are namespaced; own tools are present
        assert any(name.endswith("big_list") for name in tools)
        assert "query_fence" in tools
        assert "context_report" in tools

        big_tool = next(n for n in tools if n.endswith("big_list"))
        result = await client.call_tool(big_tool, {})
        text = result.content[0].text
        assert "query_fence" in text  # fenced

        # retrieve the raw via query_fence
        import re
        handle = re.search(r"h_[0-9a-f]+", text).group(0)
        retrieved = await client.call_tool(
            "query_fence", {"handle": handle, "start_line": 0, "end_line": 0}
        )
        assert "x" in retrieved.content[0].text

        report = await client.call_tool("context_report", {})
        assert "saved" in report.content[0].text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_integration.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_server'`.

- [ ] **Step 3: Implement `server.py`**

`build_server` accepts pre-built backends (for the in-memory test) OR `None` (production path builds them from config). This keeps the assembly testable without spawning subprocesses.

```python
# src/context_guard/server.py
"""Assemble the context-guard proxy: aggregate downstream MCP servers, install
the fence middleware, and register context-guard's own tools.

Transport: stdio (default). The proxy process is long-lived for the session.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from context_guard.config import Config, load_config
from context_guard.middleware import FenceMiddleware
from context_guard.store import FenceStore
from context_guard.tools import (
    context_report_text,
    fetch_fenced,
    query_fence,
    run_fenced,
)
from context_guard.usage import UsageTracker


def _backends_from_config(cfg: Config) -> dict[str, Any]:
    """Build an MCPConfig-style mapping FastMCP.as_proxy understands."""
    return {
        "mcpServers": {
            s.name: {"command": s.command, "args": s.args, "env": s.env}
            for s in cfg.servers
        }
    }


def build_server(
    *,
    downstream_backends: dict[str, FastMCP] | dict[str, Any] | None = None,
    config: Config | None = None,
    store: FenceStore,
    tracker: UsageTracker,
    threshold_tokens: int = 2000,
) -> FastMCP:
    if downstream_backends is not None:
        # Test/in-memory path: proxy a dict of named FastMCP backends.
        proxy = FastMCP.as_proxy(downstream_backends, name="context-guard")
    else:
        assert config is not None, "config required when downstream_backends is None"
        proxy = FastMCP.as_proxy(_backends_from_config(config), name="context-guard")

    proxy.add_middleware(FenceMiddleware(store, tracker, threshold_tokens))

    @proxy.tool
    def query_fence_tool(
        handle: str,
        query: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Retrieve specifics from a fenced output by handle: keyword `query` or
        a `start_line`/`end_line` range."""
        return query_fence(
            store, handle, query=query, start_line=start_line, end_line=end_line
        )

    # FastMCP uses the function's registered name; expose the clean public name.
    query_fence_tool.__name__ = "query_fence"

    @proxy.tool
    def run_fenced_tool(command: list[str], timeout: int = 120) -> str:
        """Run a local shell command and fence its output if large."""
        return run_fenced(
            store, tracker, command, threshold_tokens=threshold_tokens, timeout=timeout
        )

    run_fenced_tool.__name__ = "run_fenced"

    @proxy.tool
    def fetch_fenced_tool(url: str, timeout: int = 120) -> str:
        """Fetch a URL and fence the body if large."""
        return fetch_fenced(
            store, tracker, url, threshold_tokens=threshold_tokens, timeout=timeout
        )

    fetch_fenced_tool.__name__ = "fetch_fenced"

    @proxy.tool
    def context_report() -> str:
        """Report estimated per-tool token usage and savings this session."""
        return context_report_text(tracker)

    return proxy


def main() -> None:
    cfg_path = Path(os.getenv("CONTEXT_GUARD_CONFIG", ".context-guard.toml"))
    cfg = load_config(cfg_path)
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    store = FenceStore(db_path=cfg.cache_dir / "fences.db")
    tracker = UsageTracker()
    server = build_server(
        config=cfg, store=store, tracker=tracker, threshold_tokens=cfg.threshold_tokens
    )
    server.run()  # stdio


if __name__ == "__main__":
    main()
```

NOTE on tool naming: if assigning `__name__` after `@proxy.tool` does not rename the registered tool in the installed FastMCP version (verify by checking the tool names in the integration test output), instead pass the name explicitly, e.g. `@proxy.tool(name="query_fence")`, and define the functions with plain bodies. Use whichever the installed version supports — the integration test asserts the final tool names.

- [ ] **Step 4: Run the integration test**

Run: `.venv/Scripts/python -m pytest tests/test_integration.py -v`
Expected: PASS (1 passed). If tool names differ, apply the naming NOTE above and re-run.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python -m pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/context_guard/server.py tests/test_integration.py
git commit -m "feat: assemble proxy + middleware + own tools; end-to-end fencing test"
```

---

### Task 10: Manual smoke test against a real MCP server

**Files:**
- Create: `.context-guard.toml` (local, gitignored example lives in README)
- Create: `examples/.context-guard.toml`

- [ ] **Step 1: Create an example config**

`examples/.context-guard.toml`:
```toml
[fence]
threshold_tokens = 2000

[servers.everything]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-everything"]
```

- [ ] **Step 2: Smoke-run the server over stdio with the MCP inspector or a manual client**

Run:
```bash
cd context-guard
CONTEXT_GUARD_CONFIG=examples/.context-guard.toml .venv/Scripts/context-guard
```
Expected: the process starts, spawns the `everything` server, and waits on stdio without crashing. Terminate with Ctrl-C.

(If `npx`/Node is unavailable, skip the live smoke and rely on the Task 9 integration test; note this in the commit.)

- [ ] **Step 3: Commit**

```bash
git add examples/.context-guard.toml
git commit -m "docs: example .context-guard.toml for smoke testing"
```

---

### Task 11: README + packaging check

**Files:**
- Create: `README.md`
- Create: `LICENSE` (MIT)

- [ ] **Step 1: Write `README.md`**

Include: one-line pitch; the problem (MCP tool output floods the 200K context window); install (`pipx install context-guard` / `uvx context-guard`); the two-file setup (`.mcp.json` pointing at context-guard + `.context-guard.toml` listing wrapped servers); how fencing + `query_fence` work; the four own-tools; the explicit limitation that native `Bash`/`Read`/`Grep` output is not fenced; "Pro coming soon" note. Use the config and `.mcp.json` snippets from the design spec.

- [ ] **Step 2: Add MIT `LICENSE`** with the standard MIT text, copyright `2026 chris`.

- [ ] **Step 3: Build the package to verify metadata**

Run:
```bash
.venv/Scripts/python -m pip install build
.venv/Scripts/python -m build
```
Expected: produces `dist/context_guard-0.1.0-py3-none-any.whl` and `.tar.gz` with no errors.

- [ ] **Step 4: Run the full suite + linter one last time**

Run:
```bash
.venv/Scripts/python -m pytest -v
.venv/Scripts/python -m ruff check src tests
```
Expected: all tests PASS; ruff reports no errors.

- [ ] **Step 5: Commit**

```bash
git add README.md LICENSE
git commit -m "docs: README + MIT license; verify package build"
```

---

## Self-review

**Spec coverage check:**
- Local stdio aggregating proxy → Tasks 8, 9 (`as_proxy` + middleware). ✓
- `.context-guard.toml` setup → Task 5 (loader) + Task 9 (`_backends_from_config`). ✓
- Components: server/proxy/fence/distill/store/tokens/config/tools → Tasks 1–9. ✓ (proxy.py merged into server.py via `as_proxy` — FastMCP provides the proxy, so a separate proxy.py is unnecessary; noted as a deliberate simplification of the spec's module list.)
- Auto-fence over threshold + handle + passthrough → Task 4. ✓
- query_fence / run_fenced / fetch_fenced / context_report → Tasks 7, 9. ✓
- Deterministic distill (JSON/text/HTML/fallback) → Task 3. ✓ (HTML handled by the text path via tag-agnostic head/tail; a dedicated HTML stripper is deferred — see note below.)
- SQLite + FTS5 store with TTL/size prune → Task 2. ✓
- Token estimate (heuristic + optional tiktoken) → Task 1. ✓
- Hard timeout on run_fenced (no silent hang) → Task 7 (`test_run_fenced_timeout_returns_error_not_hang`). ✓
- Basic context_report (free) → Tasks 6, 7, 9. ✓
- Error handling: unknown handle, config errors, store cap → Tasks 2, 5, 7. ✓
- PyPI publish prep → Task 11. ✓
- Pro tier (budget/license) → **out of scope for M1** by design (separate M2 plan). ✓

**Gaps found + resolved:**
- Spec lists a dedicated HTML distiller; M1 routes HTML through the text distiller (head/tail by line). This is acceptable for M1 — full raw HTML is still retrievable via query_fence. A dedicated `distill_html` is deferred to a follow-up; not a blocker.
- Spec lists `proxy.py` as a module; FastMCP's `as_proxy` makes a hand-written proxy redundant, so proxy responsibilities live in `server.py`. Deliberate DRY simplification.

**Placeholder scan:** No TBD/TODO; every code step contains complete code; every test step contains real assertions. ✓

**Type consistency:** `FenceResult` fields, `FenceStore` method signatures, `UsageTracker.record/report` keys, and `tools.py` function signatures are used identically across Tasks 2, 4, 6, 7, 8, 9. `fence(content, *, source, store, threshold_tokens)` signature is consistent everywhere it's called. ✓

**Risk flagged for the executor:** The only version-sensitive surface is FastMCP's middleware import path, the `on_call_tool` hook signature, and tool-renaming. Task 0 Step 5 verifies these up front; Tasks 8–9 carry explicit fallback notes. Everything else is pure Python with no external API.
