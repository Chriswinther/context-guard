"""Logic for context-guard's own tools. Pure functions wrapped by server.py."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from context_guard.fence import fence
from context_guard.store import FenceStore
from context_guard.usage import UsageTracker, savings_readout


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
    # Parity with the proxy middleware: a fenced result carries the cumulative
    # savings readout; small pass-through results stay clean. The tracker is
    # already updated above, so the total is current.
    if res.fenced:
        return f"{res.text}\n\n{savings_readout(tracker)}"
    return res.text


# Bare interpreter names that, on Windows, often resolve to the Microsoft Store
# app-execution-alias stub — which blocks forever in a non-interactive
# subprocess. Map these to the interpreter actually running context-guard.
_PYTHON_NAMES = {"python", "python3", "python.exe", "python3.exe"}


def _augmented_path() -> str:
    """PATH with the running interpreter's dir + Scripts prepended, so sibling
    tools (pip, ruff, …) installed alongside it resolve even when the host
    launched context-guard with a stripped PATH."""
    py_dir = Path(sys.executable).parent
    return os.pathsep.join(
        [str(py_dir), str(py_dir / "Scripts"), os.environ.get("PATH", "")]
    )


def _resolve_command(command: list[str], path: str) -> tuple[list[str] | None, str | None]:
    """Resolve command[0] to a runnable executable, working around Windows
    quirks. Returns (resolved_command, error_message); exactly one is non-None.
    """
    if not command:
        return None, "run_fenced: empty command"
    exe, *rest = command
    if exe.lower() in _PYTHON_NAMES:
        return [sys.executable, *rest], None
    found = shutil.which(exe, path=path)
    if found is None:
        return None, (
            f"run_fenced: command not found: {exe!r}. It is not on PATH for the "
            f"context-guard process — use an absolute path or 'python -m <module>'."
        )
    if "windowsapps" in found.lower():
        return None, (
            f"run_fenced: {exe!r} resolves to a Microsoft Store alias stub "
            f"({found}) which hangs non-interactively; refusing to run it. Use "
            f"an absolute path to the real executable."
        )
    return [found, *rest], None


def run_fenced(
    store: FenceStore,
    tracker: UsageTracker,
    command: list[str],
    *,
    threshold_tokens: int = 2000,
    timeout: int = 120,
) -> str:
    path = _augmented_path()
    resolved, err = _resolve_command(command, path)
    if err is not None:
        return err
    env = {**os.environ, "PATH": path}
    try:
        proc = subprocess.run(
            resolved,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"run_fenced: timeout after {timeout}s: {command!r}"
    except OSError as e:
        return f"run_fenced: failed to start {command!r}: {e}"
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
