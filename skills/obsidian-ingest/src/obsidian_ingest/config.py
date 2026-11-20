"""Load trusted vault scope and draft destinations for CLI and MCP curation.

Configuration grants filesystem access; acquired source text cannot extend this policy.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from obsidian_ingest.drafts import _draft_folder_parts


@dataclass(frozen=True, slots=True)
class VaultSettings:
    """Trusted root, readable scope, exclusions, and create-only publication destination."""

    root: Path
    allowed: tuple[str, ...]
    excluded: tuple[str, ...]
    draft_folder: str = "00 Inbox/AI Drafts"


def default_config_path() -> Path:
    """Return the trusted settings path within this installed skill directory."""
    return Path(__file__).resolve().parents[2] / "config/settings.toml"


def _relative_parts(value: object, label: str) -> tuple[str, ...]:
    """Validate a relative policy path, raising a label-specific ValueError if unsafe."""
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


@dataclass(frozen=True, slots=True)
class ArticleSettings:
    """Trusted network policy for fetching web pages; never set by a tool argument."""

    allow_private_network: bool = False
    extra_ports: tuple[int, ...] = ()


def article_settings(data: dict) -> ArticleSettings:
    """Validate the optional `[articles]` table of parsed configuration."""
    section = data.get("articles", {})
    if not isinstance(section, dict) or set(section) - {"allow_private_network", "extra_ports"}:
        raise ValueError("[articles] accepts only allow_private_network and extra_ports")
    private = section.get("allow_private_network", False)
    ports = section.get("extra_ports", [])
    if not isinstance(private, bool):
        raise TypeError("articles.allow_private_network must be true or false")
    if not isinstance(ports, list) or any(
        isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
        for port in ports
    ):
        raise ValueError("articles.extra_ports must be an array of port numbers")
    return ArticleSettings(private, tuple(sorted(set(ports))))


def load_article_settings(path: Path) -> ArticleSettings:
    """Read only the article network policy; a missing file means the safe defaults."""
    if not path.is_file():
        return ArticleSettings()
    try:
        with path.open("rb") as file:
            return article_settings(tomllib.load(file))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Cannot load trusted configuration: {error}") from error


_VAULT_CONFIG_SHAPE = "Configuration must contain a [vault] table and optionally [articles]"


def load_vault_settings(path: Path) -> VaultSettings:
    """Load a required trusted `[vault]` section."""
    settings = load_optional_vault_settings(path)
    if settings is None:
        raise ValueError(_VAULT_CONFIG_SHAPE)
    return settings


def load_optional_vault_settings(path: Path) -> VaultSettings | None:
    """Allow an absent `[vault]` section, but never ignore a malformed one."""
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"Cannot load trusted configuration: {error}") from error
    if set(data) - {"vault", "articles"} or (
        "vault" in data and not isinstance(data["vault"], dict)
    ):
        raise ValueError(_VAULT_CONFIG_SHAPE)
    article_settings(data)
    if "vault" not in data:
        return None
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
    # Publication paths also need portable components, beyond read-scope path safety.
    _draft_folder_parts(draft_folder)
    return VaultSettings(root.resolve(strict=True), allowed, excluded, draft_folder)
