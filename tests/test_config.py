from pathlib import Path

import pytest

from obsidian_ingest.config import default_config_path, load_vault_settings


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "Engineering").mkdir(parents=True)
    return root


def _write(tmp_path: Path, text: str) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(text, encoding="utf-8")
    return config


def test_default_config_is_resolved_from_the_installed_skill() -> None:
    skill = Path(__file__).resolve().parents[1] / "skills/obsidian-ingest"
    assert default_config_path() == skill / "config/settings.toml"


def test_valid_configuration_is_normalized_and_defaults_the_draft_folder(tmp_path, vault) -> None:
    config = _write(
        tmp_path,
        f'[vault]\nroot = "{vault}"\nallowed = ["Engineering", "00 Inbox"]\n'
        'excluded = ["Engineering/Private"]\n',
    )
    settings = load_vault_settings(config)
    assert settings.root == vault.resolve()
    assert settings.allowed == ("Engineering", "00 Inbox")
    assert settings.excluded == ("Engineering/Private",)
    assert settings.draft_folder == "00 Inbox/AI Drafts"
    no_exclusions = _write(tmp_path, f'[vault]\nroot = "{vault}"\nallowed = ["*"]\n')
    assert load_vault_settings(no_exclusions).excluded == ()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('allowed = ["*", "Engineering"]', "'\\*' must be the only entry"),
        ('allowed = ["/tmp/private"]', "empty, '.' or '..'"),
        ('allowed = ["../outside"]', "empty, '.' or '..'"),
        ('allowed = ["Engineering/"]', "empty, '.' or '..'"),
        ('allowed = ["a\\\\b"]', "vault.allowed entry must be a nonempty vault-relative path"),
        ('allowed = ["a\\u0001b"]', "vault.allowed entry must be a nonempty vault-relative path"),
        ('allowed = [""]', "vault.allowed entry must be a nonempty vault-relative path"),
        ('allowed = ["*"]\ntypo = true', "Unknown vault configuration keys: typo"),
        ("allowed = []", "vault.allowed must be a nonempty array"),
        ('allowed = "*"', "vault.allowed must be a nonempty array"),
        ("allowed = [1]", "vault.allowed must be a nonempty array"),
        ('allowed = ["*"]\nexcluded = "Private"', "vault.excluded must be an array"),
        ('allowed = ["*"]\nexcluded = [1]', "vault.excluded must be an array"),
        ('allowed = ["*"]\nexcluded = ["../x"]', "vault.excluded entry"),
        ('allowed = ["Engineering", "Engineering"]', "must not contain duplicates"),
        ('allowed = ["*"]\nexcluded = ["A", "A"]', "must not contain duplicates"),
        ('allowed = ["*"]\ndraft_folder = "../outside"', "vault.draft_folder must not contain"),
        ('allowed = ["*"]\ndraft_folder = 5', "vault.draft_folder must be a nonempty"),
    ],
)
def test_malformed_or_unsafe_vault_section_is_rejected(
    tmp_path, vault, body: str, message: str
) -> None:
    config = _write(tmp_path, f'[vault]\nroot = "{vault}"\n{body}\n')
    with pytest.raises(ValueError, match=message):
        load_vault_settings(config)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[vault]\nroot = "relative/vault"\nallowed = ["*"]\n', "vault.root must be an absolute"),
        ('[vault]\nallowed = ["*"]\n', "vault.root must be an absolute"),
        ('[vault]\nroot = "/definitely/missing/vault"\nallowed = ["*"]\n', "existing directory"),
        ("[other]\nkey = 1\n", "only a \\[vault\\] table"),
        ("vault = 1\n", "only a \\[vault\\] table"),
        ("[vault\n", "Cannot load trusted configuration"),
    ],
    ids=["relative-root", "missing-root", "absent-root", "wrong-table", "not-a-table", "toml"],
)
def test_configuration_file_shape_and_root_are_checked(tmp_path, text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_vault_settings(_write(tmp_path, text))


def test_missing_configuration_file_is_a_load_error(tmp_path) -> None:
    with pytest.raises(ValueError, match="Cannot load trusted configuration"):
        load_vault_settings(tmp_path / "missing.toml")


def test_configured_root_must_be_a_directory(tmp_path) -> None:
    note = tmp_path / "note.md"
    note.write_text("not a vault", encoding="utf-8")
    config = _write(tmp_path, f'[vault]\nroot = "{note}"\nallowed = ["*"]\n')
    with pytest.raises(ValueError, match="vault.root must be an existing directory"):
        load_vault_settings(config)
