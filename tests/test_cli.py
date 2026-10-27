import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from obsidian_ingest import cli
from obsidian_ingest.config import default_config_path, load_vault_settings
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.vault import VaultReader


def test_transcript_subcommand_calls_existing_printer(monkeypatch, capsys) -> None:
    printer = MagicMock()
    url = "https://youtu.be/example"
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "transcript", url])
    monkeypatch.setattr(cli, "print_transcript", printer)

    cli.main()

    printer.assert_called_once_with(url)
    assert capsys.readouterr().out == ""


def test_metadata_subcommand_prints_normalized_json(monkeypatch, capsys) -> None:
    url = "https://youtu.be/abc123"
    metadata = normalize_youtube_metadata({"id": "abc123", "title": "A video", "timestamp": 0}, url)
    fetch_metadata = MagicMock(return_value=metadata)
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "metadata", url])
    monkeypatch.setattr(cli, "fetch_metadata", fetch_metadata)

    cli.main()

    fetch_metadata.assert_called_once_with(url)
    output = json.loads(capsys.readouterr().out)
    assert output["source_id"] == "abc123"
    assert output["title"] == "A video"
    assert output["published_at"] == "1970-01-01T00:00:00+00:00"
    assert output["chapters"] == []


def test_inspect_subcommand_formats_ingested_source(monkeypatch, capsys) -> None:
    url = "https://youtu.be/example"
    source = object()
    inspect_source = MagicMock(return_value=source)
    format_source_inspection = MagicMock(return_value="Sponsor filtering: failed")
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "inspect", url])
    monkeypatch.setattr("obsidian_ingest.inspection.inspect_source", inspect_source)
    monkeypatch.setattr(
        "obsidian_ingest.inspection.format_source_inspection", format_source_inspection
    )

    cli.main()

    inspect_source.assert_called_once_with(url)
    format_source_inspection.assert_called_once_with(source)
    assert capsys.readouterr().out == "Sponsor filtering: failed\n"


def test_mcp_subcommand_uses_trusted_vault_and_draft_folder(monkeypatch) -> None:
    run_mcp = MagicMock()
    vault = Path("/tmp/test-vault")
    monkeypatch.setattr(
        sys,
        "argv",
        ["obsidian-ingest", "mcp", "--vault", str(vault), "--draft-folder", "Inbox/Drafts"],
    )
    monkeypatch.setattr(cli, "_run_mcp", run_mcp)

    cli.main()

    run_mcp.assert_called_once_with(vault, "Inbox/Drafts", None, config_path=None)


def test_mcp_subcommand_defaults_draft_folder(monkeypatch) -> None:
    run_mcp = MagicMock()
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "mcp", "--vault", "/tmp/vault"])
    monkeypatch.setattr(cli, "_run_mcp", run_mcp)

    cli.main()

    run_mcp.assert_called_once_with(Path("/tmp/vault"), None, None, config_path=None)


def test_mcp_subcommand_passes_cache_override(monkeypatch, tmp_path) -> None:
    run_mcp = MagicMock()
    database = tmp_path / "cache.sqlite3"
    monkeypatch.setattr(
        sys,
        "argv",
        ["obsidian-ingest", "mcp", "--vault", "/tmp/vault", "--cache-db", str(database)],
    )
    monkeypatch.setattr(cli, "_run_mcp", run_mcp)
    cli.main()
    run_mcp.assert_called_once_with(Path("/tmp/vault"), None, database, config_path=None)


def test_mcp_config_argument_forwards_trusted_config(monkeypatch, tmp_path) -> None:
    run_mcp = MagicMock()
    config = tmp_path / "vault.toml"
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "mcp", "--config", str(config)])
    monkeypatch.setattr(cli, "_run_mcp", run_mcp)
    cli.main()
    run_mcp.assert_called_once_with(None, None, None, config_path=config)


def test_default_config_is_resolved_from_the_installed_skill() -> None:
    skill = Path(__file__).resolve().parents[1] / "skills/obsidian-ingest"
    assert default_config_path() == skill / "config/settings.toml"


def test_setup_requires_explicit_scope_and_never_overwrites_settings(tmp_path) -> None:
    vault = tmp_path / "vault"
    (vault / "Notes").mkdir(parents=True)
    config = tmp_path / "config/settings.toml"
    config.parent.mkdir()
    answers = iter([str(vault), "Notes,00 Inbox/AI Drafts", "Private", "00 Inbox/AI Drafts"])
    assert cli._setup(config, lambda _: next(answers)) == config
    settings = load_vault_settings(config)
    assert settings.root == vault
    assert settings.allowed == ("Notes", "00 Inbox/AI Drafts")
    assert settings.excluded == ("Private",)
    assert config.stat().st_mode & 0o777 == 0o600

    config.write_text("preserve me", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        cli._setup(config, lambda _: pytest.fail("must not prompt before overwrite check"))
    assert config.read_text(encoding="utf-8") == "preserve me"


def test_cli_vault_search_and_path_read_use_scoped_configuration(
    monkeypatch, tmp_path, capsys
) -> None:
    vault = tmp_path / "vault"
    (vault / "Notes").mkdir(parents=True)
    (vault / "Private").mkdir()
    (vault / "Notes/Retry.md").write_text("A retry budget limits load.", encoding="utf-8")
    (vault / "Private/Secret.md").write_text("must not leak", encoding="utf-8")
    config = tmp_path / "settings.toml"
    config.write_text(
        f'[vault]\nroot = "{vault}"\nallowed = ["*"]\nexcluded = ["Private"]\n',
        encoding="utf-8",
    )
    with VaultReader(load_vault_settings(config)) as reader:
        reference = reader.search("retry")["notes"][0]

    monkeypatch.setattr(
        sys,
        "argv",
        ["obsidian-ingest", "search-vault", "retry", "--config", str(config)],
    )
    cli.main()
    search = json.loads(capsys.readouterr().out)
    assert [item["path"] for item in search["notes"]] == ["Notes/Retry.md"]
    assert "must not leak" not in json.dumps(search)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "obsidian-ingest",
            "read-vault-note",
            reference["path"],
            "--revision",
            reference["revision"],
            "--config",
            str(config),
        ],
    )
    cli.main()
    note = json.loads(capsys.readouterr().out)
    assert note["path"] == "Notes/Retry.md"
    assert note["text"] == "A retry budget limits load."
    assert "note_id" not in note


def test_cli_create_draft_and_edit_suggestion_preserve_canonical_note(
    monkeypatch, tmp_path, capsys
) -> None:
    vault = tmp_path / "vault"
    target_path = "Notes/Retry.md"
    target = vault / target_path
    target.parent.mkdir(parents=True)
    target.write_text("Canonical note stays unchanged.", encoding="utf-8")
    (vault / "00 Inbox/AI Drafts").mkdir(parents=True)
    config = tmp_path / "settings.toml"
    config.write_text(
        f'[vault]\nroot = "{vault}"\nallowed = ["*"]\nexcluded = []\n',
        encoding="utf-8",
    )
    with VaultReader(load_vault_settings(config)) as reader:
        note = reader.search("retry")["notes"][0]
    metadata = normalize_youtube_metadata(
        {"id": "video-123", "title": "A source"},
        "https://www.youtube.com/watch?v=video-123",
    )
    source = SimpleNamespace(metadata=metadata)
    monkeypatch.setattr(cli, "_cache", lambda _: SimpleNamespace(get=lambda _: source))

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "obsidian-ingest",
            "create-draft",
            metadata.source_id,
            "Retry budget",
            "A retry budget limits aggregate load.",
            "--config",
            str(config),
            "--related-note",
            target_path,
            note["revision"],
            "Explains retry policy.",
        ],
    )
    cli.main()
    created = json.loads(capsys.readouterr().out)
    contents = Path(created["path"]).read_text(encoding="utf-8")
    assert "## Related Notes\n\n- [[Notes/Retry.md]]: Explains retry policy." in contents

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "obsidian-ingest",
            "create-edit-suggestion",
            metadata.source_id,
            target_path,
            note["revision"],
            "Add aggregate retry budget",
            "The source adds a system-wide constraint.",
            "A retry budget caps aggregate retries.",
            "--config",
            str(config),
        ],
    )
    cli.main()
    edit = json.loads(capsys.readouterr().out)
    assert Path(edit["path"]).name.startswith("(EDIT) Retry")
    assert target.read_text(encoding="utf-8") == "Canonical note stays unchanged."
