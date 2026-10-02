"""Client configuration edits stay local, explicit, narrow and recoverable."""

import json
import tomllib
from pathlib import Path

import pytest

from app.agents.configure import SERVER_NAME, AgentConfigError, AgentConnections


@pytest.fixture
def connections(tmp_path: Path) -> AgentConnections:
    home = tmp_path / "home"
    appdata = tmp_path / "roaming"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir()
    (appdata / "Claude").mkdir(parents=True)
    executable = tmp_path / "installation/voice-clone-mcp.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"isolated-test-placeholder")
    return AgentConnections(
        tmp_path / "data", home=home, appdata=appdata, mcp_executable=executable
    )


def test_discovery_never_reads_keys_into_response_or_writes(connections: AgentConnections) -> None:
    path = connections.paths["codex"]
    source = (
        '# keep this comment\nmodel = "chosen"\n'
        '[mcp_servers.other.env]\nSECRET = "private-test-value"\n'
    )
    path.write_text(source)
    result = connections.status()
    assert "private-test-value" not in json.dumps(result)
    assert path.read_text() == source
    assert not list(path.parent.glob("*backup*"))
    assert result["activity"] is None
    assert all(c["detected"] for c in result["clients"])


def test_codex_edit_preserves_comments_and_other_server(connections: AgentConnections) -> None:
    pytest.importorskip("tomlkit")
    path = connections.paths["codex"]
    source = (
        '# model preference\nmodel = "chosen"\n'
        '[mcp_servers.other]\ncommand = "other.exe" # intact\n'
    )
    path.write_text(source)
    result = connections.configure("codex")
    document = tomllib.loads(path.read_text())
    assert document["model"] == "chosen"
    assert document["mcp_servers"]["other"] == {"command": "other.exe"}
    assert document["mcp_servers"][SERVER_NAME] == connections.launch_entry("codex")
    assert "# model preference" in path.read_text() and "# intact" in path.read_text()
    backups = list(path.parent.glob("config.toml.voice-clone-backup-*"))
    assert len(backups) == 1 and backups[0].read_text() == source
    assert result["restart_required"] and result["backup_created"]
    assert connections.configure("codex")["changed"] is False
    assert len(list(path.parent.glob("*backup*"))) == 1


@pytest.mark.parametrize("client", ["claude_desktop", "claude_code"])
def test_json_preserves_other_servers_and_client_settings(
    connections: AgentConnections, client: str
) -> None:
    path = connections.paths[client]
    source = {
        "theme": "dark",
        "projects": {"project": {"allow": True}},
        "mcpServers": {"other": {"command": "other", "env": {"PRIVATE": "keep"}}},
    }
    path.write_text(json.dumps(source))
    connections.configure(client)
    result = json.loads(path.read_text())
    assert result["theme"] == source["theme"]
    assert result["projects"] == source["projects"]
    assert result["mcpServers"]["other"] == source["mcpServers"]["other"]
    assert result["mcpServers"][SERVER_NAME] == connections.launch_entry(client)
    assert "env" not in result["mcpServers"][SERVER_NAME]


def test_conflicting_entry_requires_explicit_replacement(connections: AgentConnections) -> None:
    path = connections.paths["claude_code"]
    source = json.dumps({"mcpServers": {SERVER_NAME: {"command": "old"}}})
    path.write_text(source)
    with pytest.raises(AgentConfigError, match="explicitly approve"):
        connections.configure("claude_code")
    assert path.read_text() == source
    connections.configure("claude_code", replace_existing=True)
    assert json.loads(path.read_text())["mcpServers"][SERVER_NAME]["command"].endswith(
        "voice-clone-mcp.exe"
    )


@pytest.mark.parametrize("source", ["{broken", "[]", '{"mcpServers": []}'])
def test_malformed_config_is_never_overwritten(connections: AgentConnections, source: str) -> None:
    path = connections.paths["claude_code"]
    path.write_text(source)
    with pytest.raises(AgentConfigError):
        connections.configure("claude_code")
    assert path.read_text() == source


def test_failed_atomic_replace_leaves_original_and_no_temp(
    connections: AgentConnections, monkeypatch
) -> None:
    path = connections.paths["claude_code"]
    path.write_text('{"other": "retained"}')
    monkeypatch.setattr(
        "app.agents.configure.os.replace", lambda *_args: (_ for _ in ()).throw(OSError("test"))
    )
    with pytest.raises(OSError):
        connections.configure("claude_code")
    assert json.loads(path.read_text()) == {"other": "retained"}
    assert not list(path.parent.glob(".voice-clone-*"))


def test_missing_mcp_blocks_config_and_unknown_client_is_rejected(
    connections: AgentConnections,
) -> None:
    connections.executable.unlink()
    assert not connections.status()["mcp_available"]
    with pytest.raises(AgentConfigError):
        connections.configure("codex")
    with pytest.raises(AgentConfigError, match="Unknown"):
        connections.configure("arbitrary-path")


def test_activity_records_only_minimal_evidence(connections: AgentConnections) -> None:
    connections.record_activity("/api/generate")
    result = connections.status()["activity"]
    assert set(result) == {"tool", "last_tool_call_at"}
    assert result["tool"] == "/api/generate"
    connections.record_activity("/api/jobs/9?untrusted-secret")
    assert connections.status()["activity"]["tool"] == "/api/jobs"


def test_symlink_target_refused(connections: AgentConnections, tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    target.write_text("{}")
    path = connections.paths["claude_code"]
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("Windows account cannot create symlinks")
    with pytest.raises(AgentConfigError, match="symbolic link"):
        connections.configure("claude_code")
    assert target.read_text() == "{}"
