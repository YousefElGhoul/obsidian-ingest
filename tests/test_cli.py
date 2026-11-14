import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from obsidian_ingest import cli, source_cache
from obsidian_ingest.config import default_config_path, load_vault_settings
from obsidian_ingest.extractor.metadata import ExcludedRange, ExclusionLookup, ExclusionStatus
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.source import Source
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


def test_setup_flags_create_settings_without_prompting(tmp_path, monkeypatch, capsys) -> None:
    vault = tmp_path / "vault"
    (vault / "Notes/Private").mkdir(parents=True)
    config = tmp_path / "settings.toml"
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("flags must not prompt"))
    base = ["obsidian-ingest", "setup", "--config", str(config), "--vault-root", str(vault)]

    monkeypatch.setattr("sys.argv", base)
    with pytest.raises(ValueError, match="at least one --allow"):
        cli.main()
    assert not config.exists()

    flags = ["--allow", "Notes", "--allow", "00 Inbox/AI Drafts", "--exclude", "Notes/Private"]
    monkeypatch.setattr("sys.argv", [*base, *flags])
    cli.main()
    settings = load_vault_settings(config)
    assert settings.root == vault
    assert settings.allowed == ("Notes", "00 Inbox/AI Drafts")
    assert settings.excluded == ("Notes/Private",)
    assert settings.draft_folder == "00 Inbox/AI Drafts"
    assert config.stat().st_mode & 0o777 == 0o600
    assert "Saved trusted settings" in capsys.readouterr().out


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
            "# Retry budget: why?\n\nA retry budget limits aggregate load.",
            "--config",
            str(config),
            "--related-note",
            target_path,
            "Explains retry policy.",
        ],
    )
    cli.main()
    created = json.loads(capsys.readouterr().out)
    assert Path(created["path"]).name == "retry-budget-why.md"
    contents = Path(created["path"]).read_text(encoding="utf-8")
    assert contents.startswith("# Retry budget: why?\n\nA retry budget limits aggregate load.")
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
    assert Path(edit["path"]).name == "edit-retry.md"
    assert target.read_text(encoding="utf-8") == "Canonical note stays unchanged."


# --- Application behavior exercised through the CLI -----------------------------------

URL = "https://www.youtube.com/watch?v=video-123"


def _sample_source() -> Source:
    metadata = normalize_youtube_metadata(
        {
            "id": "video-123",
            "title": "A useful explanation",
            "channel": "A creator",
            "duration": 600,
            "chapters": [{"title": "Main idea", "start_time": 0, "end_time": 20}],
        },
        URL,
    )
    transcript = Transcript(
        "video-123",
        "en",
        "en",
        False,
        (
            TranscriptSegment("First atom", 0, 1),
            TranscriptSegment("Second atom", 1, 0.5),
            TranscriptSegment("Third atom", 1, 1),
            TranscriptSegment("Sponsor atom", 2, 1),
        ),
    )
    lookup = ExclusionLookup(
        (ExcludedRange(2, 3, "sponsor", "SponsorBlock"),),
        ExclusionStatus.COMPLETE,
        "SponsorBlock",
        ("sponsor",),
    )
    return Source(
        metadata, transcript, lookup, filter_source_transcript(metadata, transcript, lookup)
    )


def _run(monkeypatch, capsys, *args: str) -> tuple[int, Any, dict | None]:
    """Run one CLI invocation; each call opens fresh cache and vault state like a new process."""
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", *args])
    code = 0
    try:
        cli.main()
    except SystemExit as error:
        code = error.code
    captured = capsys.readouterr()
    output = json.loads(captured.out) if captured.out.strip() else None
    error = json.loads(captured.err)["error"] if captured.err.strip() else None
    return code, output, error


def _ok(monkeypatch, capsys, *args: str) -> Any:
    code, output, error = _run(monkeypatch, capsys, *args)
    assert (code, error) == (0, None)
    return output


def _fail(monkeypatch, capsys, *args: str) -> dict:
    code, output, error = _run(monkeypatch, capsys, *args)
    assert code != 0 and output is None and error is not None
    return error


def _config(tmp_path: Path, vault: Path, excluded: tuple[str, ...] = ("Private",)) -> Path:
    config = tmp_path / "settings.toml"
    config.write_text(
        f'[vault]\nroot = "{vault}"\nallowed = ["*"]\nexcluded = {json.dumps(list(excluded))}\n',
        encoding="utf-8",
    )
    return config


def _vault_fixture(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "Notes").mkdir(parents=True)
    (vault / "Private").mkdir()
    (vault / "00 Inbox/AI Drafts").mkdir(parents=True)
    for index in range(5):
        (vault / f"Notes/Fixture {index}.md").write_text(f"fixture body {index}", encoding="utf-8")
    (vault / "Private/Fixture secret.md").write_text("fixture SECRET_MARKER", encoding="utf-8")
    return vault


@pytest.fixture
def fake_extraction(monkeypatch):
    source = _sample_source()
    extraction = MagicMock(side_effect=lambda url, exclude_sponsors=True: source)
    monkeypatch.setattr(source_cache, "ingest_source", extraction)
    return extraction


def test_cli_source_operations_reuse_persistent_cache(
    monkeypatch, capsys, tmp_path, fake_extraction
) -> None:
    db = ("--cache-db", str(tmp_path / "cache.db"))
    first = _ok(monkeypatch, capsys, "ingest-source", URL, *db)
    assert first["cached"] is False
    assert first["overview"]["sponsor_filtering"]["excluded_segments"] == 1
    again = _ok(monkeypatch, capsys, "ingest-source", "https://youtu.be/video-123", *db)
    assert (again["source_id"], again["cached"]) == (first["source_id"], True)
    assert fake_extraction.call_count == 1
    source_id = first["source_id"]

    # Cache reuse is known only at ingest; a later read by handle reports it as unknown.
    assert first["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] is False
    assert again["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] is True
    overview = _ok(monkeypatch, capsys, "get-source-overview", source_id, *db)
    assert overview["diagnostics"]["acquisition"]["reused_from_cache"] is None
    first["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] = None
    assert overview == first["overview"]
    assert _ok(monkeypatch, capsys, "list-chapters", source_id, *db) == [
        {"index": 0, "title": "Main idea", "start": 0.0, "end": 20.0}
    ]
    assert _ok(monkeypatch, capsys, "list-exclusions", source_id, *db)[0]["source_indices"] == [3]

    texts = []
    offset = 0
    while offset is not None:
        page = _ok(
            monkeypatch,
            capsys,
            "read-transcript",
            source_id,
            "--offset",
            str(offset),
            "--limit",
            "2",
            *db,
        )
        texts.extend(segment[3] for segment in page["segments"])
        offset = page["next_offset"]
    assert texts == ["First atom", "Second atom", "Third atom"]
    raw = _ok(monkeypatch, capsys, "read-transcript", source_id, "--include-excluded", *db)
    assert raw["fields"] == ["source_index", "start", "duration", "text"]
    assert raw["segments"][-1] == [3, 2, 1, "Sponsor atom"]
    chapter = _ok(monkeypatch, capsys, "read-chapter", source_id, "0", "--offset", "1", *db)
    assert [segment[3] for segment in chapter["segments"]] == ["Second atom", "Third atom"]

    refreshed = _ok(monkeypatch, capsys, "ingest-source", URL, "--refresh", *db)
    assert (refreshed["source_id"], refreshed["cached"]) == (source_id, False)
    assert fake_extraction.call_count == 2


def test_cli_source_errors_use_shared_codes(monkeypatch, capsys, tmp_path, fake_extraction):
    db = ("--cache-db", str(tmp_path / "cache.db"))
    source_id = _ok(monkeypatch, capsys, "ingest-source", URL, *db)["source_id"]
    cases = [
        (("get-source-overview", "missing"), "SOURCE_NOT_FOUND", "Unknown source_id"),
        (("read-transcript", source_id, "--limit", "0"), "INVALID_ARGUMENT", "limit must be"),
        (("read-transcript", source_id, "--offset", "-1"), "INVALID_ARGUMENT", "offset must"),
        (
            ("read-transcript", source_id, "--start", "10", "--end", "1"),
            "INVALID_ARGUMENT",
            "end must not precede start",
        ),
        (("read-chapter", source_id, "4"), "INVALID_ARGUMENT", "chapter_index 4 out of range"),
    ]
    for args, code, message in cases:
        error = _fail(monkeypatch, capsys, *args, *db)
        assert error["code"] == code
        assert message in error["message"]

    code, _, error = _run(monkeypatch, capsys, "read-transcript", source_id, "--offset", "x", *db)
    assert code == 2 and error["code"] == "INVALID_ARGUMENT"
    code, _, error = _run(monkeypatch, capsys, "list-chapters", source_id, "extra", *db)
    assert code == 2 and error["code"] == "INVALID_ARGUMENT"
    fake_extraction.side_effect = RuntimeError("network down")
    error = _fail(monkeypatch, capsys, "ingest-source", URL, "--include-sponsors", *db)
    assert error["code"] == "INGEST_FAILED"


def test_cli_search_cursor_continues_across_invocations(monkeypatch, capsys, tmp_path):
    vault = _vault_fixture(tmp_path)
    config = ("--config", str(_config(tmp_path, vault)))
    paths = []
    cursor = None
    while True:
        args = ["search-vault", "fixture", "--limit", "2", *config]
        if cursor:
            args += ["--cursor", cursor]
        page = _ok(monkeypatch, capsys, *args)
        assert all(
            set(note) == {"path", "title", "revision", "matched_terms", "snippet", "linkable"}
            for note in page["notes"]
        )
        paths.extend(note["path"] for note in page["notes"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert paths == [f"Notes/Fixture {index}.md" for index in range(5)]


def test_cli_search_cursor_is_untrusted_and_bound_to_query_and_scope(monkeypatch, capsys, tmp_path):
    vault = _vault_fixture(tmp_path)
    config_path = _config(tmp_path, vault)
    config = ("--config", str(config_path))
    cursor = _ok(monkeypatch, capsys, "search-vault", "fixture", "--limit", "2", *config)[
        "next_cursor"
    ]
    for args in (
        ("search-vault", "fixture", "--cursor", "not a cursor"),
        ("search-vault", "fixture", "--cursor", base64.urlsafe_b64encode(b"[1]").decode()),
        ("search-vault", "other", "--cursor", cursor),
        ("search-vault", "fixture", "--scope", "title", "--cursor", cursor),
    ):
        assert _fail(monkeypatch, capsys, *args, *config)["code"] == "INVALID_CURSOR"

    # Narrowing the trusted scope invalidates outstanding cursors.
    _config(tmp_path, vault, ("Private", "Notes/Fixture 4.md"))
    error = _fail(monkeypatch, capsys, "search-vault", "fixture", "--cursor", cursor, *config)
    assert error["code"] == "INVALID_CURSOR"

    # A forged cursor that matches the current policy is only an offset: it never
    # surfaces excluded notes.
    with VaultReader(load_vault_settings(config_path)) as reader:
        forged = reader._cursor("fixture", "both", 0)
    page = _ok(monkeypatch, capsys, "search-vault", "fixture", "--cursor", forged, *config)
    serialized = json.dumps(page)
    assert "Private" not in serialized and "SECRET_MARKER" not in serialized
    assert "Notes/Fixture 4.md" not in serialized


def test_cli_search_cursor_survives_separate_processes(tmp_path):
    vault = _vault_fixture(tmp_path)
    config = _config(tmp_path, vault)
    code = (
        "import sys; from obsidian_ingest.cli import main; sys.argv[0] = 'obsidian-ingest'; main()"
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "skills/obsidian-ingest/src"),
    }

    def run(*args: str) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                "search-vault",
                "fixture",
                "--limit",
                "3",
                "--config",
                str(config),
                *args,
            ],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return json.loads(completed.stdout)

    first = run()
    second = run("--cursor", first["next_cursor"])
    assert [note["path"] for note in second["notes"]] == [
        "Notes/Fixture 3.md",
        "Notes/Fixture 4.md",
    ]
    assert second["next_cursor"] is None


def test_cli_note_reads_check_revisions_and_exclusions(monkeypatch, capsys, tmp_path):
    vault = _vault_fixture(tmp_path)
    note = vault / "Notes/Fixture 0.md"
    note.write_text("0123456789abcdef", encoding="utf-8")
    config = ("--config", str(_config(tmp_path, vault)))
    first = _ok(
        monkeypatch, capsys, "read-vault-note", "Notes/Fixture 0.md", "--limit", "10", *config
    )
    assert (first["text"], first["next_offset"]) == ("0123456789", 10)
    assert "note_id" not in first
    error = _fail(
        monkeypatch, capsys, "read-vault-note", "Notes/Fixture 0.md", "--offset", "10", *config
    )
    assert error["code"] == "INVALID_ARGUMENT" and "revision is required" in error["message"]
    rest = _ok(
        monkeypatch,
        capsys,
        "read-vault-note",
        "Notes/Fixture 0.md",
        "--offset",
        "10",
        "--revision",
        first["revision"],
        *config,
    )
    assert rest["text"] == "abcdef" and rest["next_offset"] is None

    note.write_text("changed by a human", encoding="utf-8")
    error = _fail(
        monkeypatch,
        capsys,
        "read-vault-note",
        "Notes/Fixture 0.md",
        "--offset",
        "10",
        "--revision",
        first["revision"],
        *config,
    )
    assert error["code"] == "NOTE_CHANGED"
    for path in ("Private/Fixture secret.md", "../outside.md", "Notes/Missing.md", "Notes"):
        error = _fail(monkeypatch, capsys, "read-vault-note", path, *config)
        assert error == {"code": "NOTE_UNAVAILABLE", "message": "note is unavailable"}


def test_cli_missing_config_is_reported(monkeypatch, capsys, tmp_path):
    error = _fail(monkeypatch, capsys, "search-vault", "--config", str(tmp_path / "missing.toml"))
    assert error["code"] == "CONFIG_ERROR"


def test_cli_publication_is_create_only_with_verified_links(
    monkeypatch, capsys, tmp_path, fake_extraction
):
    vault = _vault_fixture(tmp_path)
    target = vault / "Notes/Fixture 1.md"
    config = ("--config", str(_config(tmp_path, vault)))
    db = ("--cache-db", str(tmp_path / "cache.db"))
    source_id = _ok(monkeypatch, capsys, "ingest-source", URL, *db)["source_id"]
    revision = _ok(monkeypatch, capsys, "read-vault-note", "Notes/Fixture 1.md", *config)[
        "revision"
    ]
    canonical = {path: path.read_bytes() for path in vault.rglob("*.md")}

    # Ordinary related links need no revision and survive harmless human edits.
    target.write_text("edited by a human", encoding="utf-8")
    canonical[target] = target.read_bytes()
    draft = _ok(
        monkeypatch,
        capsys,
        "create-draft",
        source_id,
        "# Retry budget\n\nBody.",
        "--timestamp",
        "1",
        "--related-note",
        "Notes/Fixture 1.md",
        "Shared idea.",
        *config,
        *db,
    )
    text = Path(draft["path"]).read_text(encoding="utf-8")
    assert "- [[Notes/Fixture 1.md]]: Shared idea." in text
    assert "[00:01](https://www.youtube.com/watch?v=video-123&t=1s)" in text
    assert Path(draft["path"]).name == "retry-budget.md"
    duplicate = _ok(
        monkeypatch, capsys, "create-draft", source_id, "# Retry budget\n\nBody.", *config, *db
    )
    assert Path(duplicate["path"]).name == "retry-budget-2.md"
    named = _ok(
        monkeypatch,
        capsys,
        "create-draft",
        source_id,
        "# Title: kept verbatim?\n\nBody.",
        "--filename",
        "../My_File.md",
        *config,
        *db,
    )
    assert Path(named["path"]) == vault / "00 Inbox/AI Drafts/my-file.md"
    assert Path(named["path"]).read_text(encoding="utf-8").startswith("# Title: kept verbatim?")

    drafts = vault / "00 Inbox/AI Drafts"
    before = sorted(drafts.iterdir())
    for args, code in (
        (("create-draft", source_id, "# Bad\n\n## Source\nX\n\n## More\nY"), "INVALID_ARGUMENT"),
        (("create-draft", source_id, "Body without a title"), "INVALID_ARGUMENT"),
        (("create-draft", source_id, "# Bad", "--filename", "///"), "INVALID_ARGUMENT"),
        (
            (
                "create-draft",
                source_id,
                "# Bad\n\nBody",
                "--related-note",
                "Private/Fixture secret.md",
                "Hidden.",
            ),
            "NOTE_UNAVAILABLE",
        ),
        (
            (
                "create-edit-suggestion",
                source_id,
                "Notes/Fixture 1.md",
                revision,
                "Summary",
                "Reason",
                "Proposal",
            ),
            "NOTE_CHANGED",
        ),
        (
            (
                "create-edit-suggestion",
                source_id,
                "Private/Fixture secret.md",
                revision,
                "Summary",
                "Reason",
                "Proposal",
            ),
            "NOTE_UNAVAILABLE",
        ),
        (("create-draft", "missing", "# Bad\n\nBody"), "SOURCE_NOT_FOUND"),
    ):
        error = _fail(monkeypatch, capsys, *args, *config, *db)
        assert error["code"] == code
        assert "SECRET_MARKER" not in error["message"]
    assert sorted(drafts.iterdir()) == before

    current = _ok(monkeypatch, capsys, "read-vault-note", "Notes/Fixture 1.md", *config)["revision"]
    edit = _ok(
        monkeypatch,
        capsys,
        "create-edit-suggestion",
        source_id,
        "Notes/Fixture 1.md",
        current,
        "Add detail",
        "The source adds context.",
        "New paragraph.",
        *config,
        *db,
    )
    assert Path(edit["path"]).name == "edit-fixture-1.md"
    assert Path(edit["path"]).read_text(encoding="utf-8").startswith("[[Notes/Fixture 1.md]]")
    assert {path: path.read_bytes() for path in canonical} == canonical
