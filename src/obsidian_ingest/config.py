"""Trusted TOML configuration for the local MCP server."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from obsidian_ingest.drafts import _draft_folder_parts


@dataclass(frozen=True, slots=True)
class VaultSettings:
    root: Path
    allowed: tuple[str, ...]
    excluded: tuple[str, ...]
    draft_folder: str = "00 Inbox/AI Drafts"


def _relative_parts(value: object, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{label} must be a nonempty vault-relative path")
    parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{label} must not contain empty, '.' or '..' components")
    return parts


def load_vault_settings(path: Path) -> VaultSettings:
    """Load the strict trusted `[vault]` section from a TOML configuration file."""
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Cannot load trusted configuration: {error}") from error
    if set(data) != {"vault"} or not isinstance(data["vault"], dict):
        raise ValueError("Configuration must contain only a [vault] table")
    section = data["vault"]
    allowed_keys = {"root", "allowed", "excluded", "draft_folder"}
    if set(section) - allowed_keys:
        raise ValueError(
            f"Unknown vault configuration keys: {', '.join(sorted(set(section) - allowed_keys))}"
        )
    root_value = section.get("root")
    if not isinstance(root_value, str) or not Path(root_value).is_absolute():
        raise ValueError("vault.root must be an absolute path")
    root = Path(root_value)
    if not root.is_dir():
        raise ValueError("vault.root must be an existing directory")
    allowed_value = section.get("allowed")
    if (
        not isinstance(allowed_value, list)
        or not allowed_value
        or any(not isinstance(item, str) for item in allowed_value)
    ):
        raise ValueError("vault.allowed must be a nonempty array of paths")
    if "*" in allowed_value and allowed_value != ["*"]:
        raise ValueError("'*' must be the only entry in vault.allowed")
    allowed = tuple(
        item if item == "*" else "/".join(_relative_parts(item, "vault.allowed entry"))
        for item in allowed_value
    )
    excluded_value = section.get("excluded", [])
    if not isinstance(excluded_value, list) or any(
        not isinstance(item, str) for item in excluded_value
    ):
        raise ValueError("vault.excluded must be an array of paths")
    excluded = tuple(
        "/".join(_relative_parts(item, "vault.excluded entry")) for item in excluded_value
    )
    if len(set(allowed)) != len(allowed) or len(set(excluded)) != len(excluded):
        raise ValueError("vault.allowed and vault.excluded must not contain duplicates")
    draft_folder = section.get("draft_folder", "00 Inbox/AI Drafts")
    draft_folder = "/".join(_relative_parts(draft_folder, "vault.draft_folder"))
    _draft_folder_parts(draft_folder)
    return VaultSettings(root.resolve(strict=True), allowed, excluded, draft_folder)
