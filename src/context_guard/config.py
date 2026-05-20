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
