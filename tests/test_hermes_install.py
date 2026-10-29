from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from obsidian_ingest.hermes_install import (
    MCP_TOOL_NAMES,
    InstallError,
    _get_path,
    _managed_config,
    _parse_toolsets,
    _read_vault_settings,
)


def _config(text: str) -> CommentedMap:
    data = YAML().load(text)
    assert isinstance(data, CommentedMap)
    return data


def test_managed_config_preserves_provider_and_vault_permissions(tmp_path: Path) -> None:
    data = _config(
        """
model:
  provider: local
  default: test-model
platform_toolsets:
  cli: [hermes-cli]
providers:
  local:
    api_key: secret
mcp_servers:
  other:
    command: other-mcp
"""
    )
    uv = tmp_path / "uv"
    uv.touch()
    skill = tmp_path / "skills/obsidian-ingest"
    skill.mkdir(parents=True)
    (skill / "mcp").mkdir()
    (skill / "mcp/server.py").touch()

    result, paths = _managed_config(data, skill, uv, ["terminal", "file", "skills", "project"], [])

    assert result["model"] == data["model"]
    assert result["providers"] == data["providers"]
    assert result["mcp_servers"]["other"]["enabled"] is False
    assert result["mcp_servers"]["obsidian-ingest"]["tools"]["include"] == list(MCP_TOOL_NAMES)
    assert result["platform_toolsets"]["cli"] == ["obsidian-ingest"]
    assert set(result["agent"]["disabled_toolsets"]) >= {"terminal", "file", "skills", "project"}
    assert ["agent", "disabled_toolsets"] in paths


def test_parse_toolsets_requires_hermes_listing() -> None:
    output = """Built-in toolsets (cli):
  ✓ enabled  terminal  💻 Terminal
  ✗ disabled file  📁 Files
Plugin toolsets (cli):
  ✗ disabled custom  Plugin
MCP servers:
  other all tools enabled
"""
    assert _parse_toolsets(output) == (["terminal", "file"], ["custom"])
    with pytest.raises(InstallError, match="did not report"):
        _parse_toolsets("No tool output")


def test_existing_vault_permissions_are_unchanged_and_not_silently_reconfigured(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skill"
    (skill / "config").mkdir(parents=True)
    config_path = skill / "config/settings.toml"
    config_path.write_text(
        f'[vault]\nroot = "{tmp_path}"\nallowed = ["Notes"]\nexcluded = []\ndraft_folder = "Drafts"\n',
        encoding="utf-8",
    )
    args = Namespace(vault_root=None, allowed=[], excluded=[], draft_folder="00 Inbox/AI Drafts")

    settings, replacement = _read_vault_settings(skill, args)

    assert settings.allowed == ("Notes",)
    assert settings.draft_folder == "Drafts"
    assert replacement is None

    args.vault_root = str(tmp_path)
    args.allowed = ["Elsewhere"]
    args.excluded = []
    args.reconfigure_vault = False
    with pytest.raises(InstallError, match="reconfigure-vault"):
        _read_vault_settings(skill, args)
    assert "Elsewhere" not in config_path.read_text(encoding="utf-8")


def test_get_path_marks_absent_values() -> None:
    assert _get_path({"a": {"b": 3}}, ("a", "b")) == 3
    assert _get_path({}, ("a", "b")).__class__.__name__ == "_Missing"
