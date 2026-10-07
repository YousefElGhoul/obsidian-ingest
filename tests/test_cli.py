import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

from obsidian_ingest import cli
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata


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

    run_mcp.assert_called_once_with(vault, "Inbox/Drafts")


def test_mcp_subcommand_defaults_draft_folder(monkeypatch) -> None:
    run_mcp = MagicMock()
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "mcp", "--vault", "/tmp/vault"])
    monkeypatch.setattr(cli, "_run_mcp", run_mcp)

    cli.main()

    run_mcp.assert_called_once_with(Path("/tmp/vault"), "00 Inbox/AI Drafts")
