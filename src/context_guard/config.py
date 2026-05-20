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
    # A server is EITHER a local stdio process (command/args/env) OR a remote
    # endpoint (url/headers/transport). Exactly one of command/url is set.
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    transport: str | None = None


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
        has_command = "command" in spec
        has_url = "url" in spec
        if not has_command and not has_url:
            raise ValueError(f"server '{name}' must set either 'command' (stdio) or 'url' (http)")
        if has_command and has_url:
            raise ValueError(f"server '{name}' sets both 'command' and 'url'; use exactly one")
        servers.append(
            ServerSpec(
                name=name,
                command=spec.get("command"),
                args=list(spec.get("args", [])),
                env=dict(spec.get("env", {})),
                url=spec.get("url"),
                headers=dict(spec.get("headers", {})),
                transport=spec.get("transport"),
            )
        )
    return Config(threshold_tokens=threshold, servers=servers)
