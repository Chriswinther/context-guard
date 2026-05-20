"""Assemble the context-guard proxy: aggregate downstream MCP servers, install
the fence middleware, and register context-guard's own tools. Transport: stdio.
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
    return {
        "mcpServers": {
            s.name: {"command": s.command, "args": s.args, "env": s.env}
            for s in cfg.servers
        }
    }


def _is_fastmcp_mapping(backends: Any) -> bool:
    """True when downstream_backends is a {name: FastMCP} mapping (in-memory),
    as opposed to an MCPConfig-style dict (production/config path)."""
    if not isinstance(backends, dict):
        return False
    return all(isinstance(v, FastMCP) for v in backends.values()) and bool(backends)


def build_server(
    *,
    downstream_backends: dict[str, FastMCP] | dict[str, Any] | None = None,
    config: Config | None = None,
    store: FenceStore,
    tracker: UsageTracker,
    threshold_tokens: int = 2000,
) -> FastMCP:
    if downstream_backends is not None and _is_fastmcp_mapping(downstream_backends):
        # In-memory composition: mount each named FastMCP under its namespace.
        # FastMCP.as_proxy() does NOT accept a {name: FastMCP} mapping (it treats
        # any dict as an MCPConfig), so we mount directly. Tools surface namespaced
        # as "<namespace>_<tool>" (e.g. down_big_list).
        proxy: FastMCP = FastMCP("context-guard")
        for namespace, backend in downstream_backends.items():
            proxy.mount(backend, namespace=namespace)
    elif downstream_backends is not None:
        # An MCPConfig-style dict (already in {"mcpServers": {...}} shape).
        proxy = FastMCP.as_proxy(downstream_backends, name="context-guard")
    else:
        # Production path: build an MCPConfig dict from the Config object so
        # FastMCP.as_proxy can spawn and manage the downstream stdio processes.
        # `config` must be a Config instance here (not a pre-wrapped dict).
        if config is None:
            raise ValueError("config required when downstream_backends is None")
        proxy = FastMCP.as_proxy(_backends_from_config(config), name="context-guard")

    proxy.add_middleware(FenceMiddleware(store, tracker, threshold_tokens))

    @proxy.tool(name="query_fence")
    def query_fence_tool(
        handle: str,
        query: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Retrieve specifics from a fenced output by handle: keyword `query` or a
        `start_line`/`end_line` range."""
        return query_fence(store, handle, query=query, start_line=start_line, end_line=end_line)

    @proxy.tool(name="run_fenced")
    def run_fenced_tool(command: list[str], timeout: int = 120) -> str:
        """Run a local shell command and fence its output if large."""
        return run_fenced(
            store, tracker, command, threshold_tokens=threshold_tokens, timeout=timeout
        )

    @proxy.tool(name="fetch_fenced")
    def fetch_fenced_tool(url: str, timeout: int = 120) -> str:
        """Fetch a URL and fence the body if large."""
        return fetch_fenced(
            store, tracker, url, threshold_tokens=threshold_tokens, timeout=timeout
        )

    @proxy.tool(name="context_report")
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
    server.run()


if __name__ == "__main__":
    main()
