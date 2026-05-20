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
    assert cfg.threshold_tokens == 2000


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_server_without_command_raises(tmp_path):
    p = tmp_path / ".context-guard.toml"
    p.write_text("[servers.bad]\nargs = []\n")
    with pytest.raises(ValueError):
        load_config(p)


HTTP_SAMPLE = """
[servers.github]
url = "https://api.githubcopilot.com/mcp/"
headers = { Authorization = "Bearer XYZ" }

[servers.fs]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "."]
"""


def test_load_parses_http_server(tmp_path):
    p = tmp_path / ".context-guard.toml"
    p.write_text(HTTP_SAMPLE)
    cfg = load_config(p)
    gh = next(s for s in cfg.servers if s.name == "github")
    assert gh.command is None
    assert gh.url == "https://api.githubcopilot.com/mcp/"
    assert gh.headers == {"Authorization": "Bearer XYZ"}
    fs = next(s for s in cfg.servers if s.name == "fs")
    assert fs.command == "npx" and fs.url is None


def test_backends_from_config_emits_http_and_stdio_shapes(tmp_path):
    from context_guard.server import _backends_from_config

    p = tmp_path / ".context-guard.toml"
    p.write_text(HTTP_SAMPLE)
    cfg = load_config(p)
    backends = _backends_from_config(cfg)["mcpServers"]
    assert backends["github"]["url"] == "https://api.githubcopilot.com/mcp/"
    assert backends["github"]["headers"] == {"Authorization": "Bearer XYZ"}
    assert "command" not in backends["github"]
    assert backends["fs"]["command"] == "npx"
    assert "url" not in backends["fs"]
