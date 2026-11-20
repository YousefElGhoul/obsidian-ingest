import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from articles import FakeWeb, Reply, article

from obsidian_ingest import cli
from obsidian_ingest.config import load_vault_settings
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.vault import VaultReader

URL = "https://www.youtube.com/watch?v=video-123"


def test_article_only_settings_allow_url_ingest_and_source_reads(tmp_path, monkeypatch, cli_runner):
    url = "https://example.com/article"
    FakeWeb(routes={url: Reply(article())}).install(monkeypatch)
    config = tmp_path / "settings.toml"
    config.write_text("[articles]\nextra_ports = []\n")
    monkeypatch.setattr(cli, "default_config_path", lambda: config)
    db = ("--cache-db", str(tmp_path / "sources.db"))

    source_id = cli_runner.ok("ingest-source", url, *db)["source_id"]
    page = cli_runner.ok("read-source", source_id, *db)
    assert page["segments"]
    assert cli_runner.fail("ingest-source", "--note", "clip.md", *db)["code"] == "NOTE_UNAVAILABLE"
    assert cli_runner.fail("search-vault", "article")["code"] == "CONFIG_ERROR"
    error = cli_runner.fail(
        "create-draft", source_id, "# Draft\n\nBody", "--read-token", page["read_token"], *db
    )
    assert error["code"] == "CONFIG_ERROR"


@pytest.mark.parametrize("vault", ["vault = false\n", "[vault]\nallowed = ['*']\n"])
def test_malformed_vault_settings_are_not_ignored_for_url_operations(
    tmp_path, cli_runner, fake_extraction, vault
):
    config = tmp_path / "settings.toml"
    config.write_text(vault + "[articles]\nextra_ports = []\n")
    db = ("--cache-db", str(tmp_path / "sources.db"))
    source_id = cli_runner.ok("ingest-source", URL, *db)["source_id"]
    for args in (("ingest-source", URL), ("read-source", source_id)):
        assert cli_runner.fail(*args, *db, "--config", str(config))["code"] == "CONFIG_ERROR"
    assert fake_extraction.call_count == 1


def test_transcript_subcommand_calls_existing_printer(monkeypatch, capsys) -> None:
    printer = MagicMock()
    url = "https://youtu.be/example"
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "transcript", url])
    monkeypatch.setattr(cli, "print_transcript", printer)

    cli.main()

    printer.assert_called_once_with(url)
    assert capsys.readouterr().out == ""


def test_metadata_subcommand_prints_normalized_json(monkeypatch, cli_runner) -> None:
    url = "https://youtu.be/abc123"
    metadata = normalize_youtube_metadata({"id": "abc123", "title": "A video", "timestamp": 0}, url)
    fetch_metadata = MagicMock(return_value=metadata)
    monkeypatch.setattr(cli, "fetch_metadata", fetch_metadata)

    output = cli_runner.ok("metadata", url)

    fetch_metadata.assert_called_once_with(url)
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


def test_non_tool_argument_errors_use_plain_argparse_usage(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "metadata"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: ") and '"error"' not in err


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (
            ["--vault", "/tmp/test-vault", "--draft-folder", "Inbox/Drafts"],
            ((Path("/tmp/test-vault"), "Inbox/Drafts", None), None),
        ),
        (["--vault", "/tmp/vault"], ((Path("/tmp/vault"), None, None), None)),
        (
            ["--vault", "/tmp/vault", "--cache-db", "/tmp/cache.sqlite3"],
            ((Path("/tmp/vault"), None, Path("/tmp/cache.sqlite3")), None),
        ),
        (["--config", "/tmp/vault.toml"], ((None, None, None), Path("/tmp/vault.toml"))),
    ],
    ids=["vault-and-draft-folder", "default-draft-folder", "cache-override", "trusted-config"],
)
def test_mcp_subcommand_forwards_its_options(monkeypatch, arguments, expected) -> None:
    run_mcp = MagicMock()
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "mcp", *arguments])
    monkeypatch.setattr(cli, "_run_mcp", run_mcp)

    cli.main()

    positional, config_path = expected
    run_mcp.assert_called_once_with(*positional, config_path=config_path)


@pytest.fixture
def fake_server(monkeypatch) -> MagicMock:
    """Capture create_server arguments and stand in for running the stdio server."""
    ran = []

    class Server:
        async def run_stdio_async(self) -> None:
            ran.append(True)

    create = MagicMock(return_value=Server())
    create.ran = ran
    monkeypatch.setattr("obsidian_ingest.mcp_server.create_server", create)
    return create


def test_mcp_with_a_vault_runs_a_server_without_vault_reads(
    monkeypatch, tmp_path, fake_server
) -> None:
    database = tmp_path / "cache.db"
    monkeypatch.setattr(
        sys,
        "argv",
        ["obsidian-ingest", "mcp", "--vault", str(tmp_path), "--cache-db", str(database)],
    )
    cli.main()
    (settings,), options = fake_server.call_args
    assert (settings.vault_path, settings.draft_folder) == (tmp_path, "00 Inbox/AI Drafts")
    assert options == {"cache_db": database}
    assert fake_server.ran == [True]


def test_mcp_with_a_config_exposes_its_vault_settings(tmp_path, fake_server) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "settings.toml"
    config.write_text(
        f'[vault]\nroot = "{vault}"\nallowed = ["*"]\ndraft_folder = "Inbox/AI"\n',
        encoding="utf-8",
    )
    cli._run_mcp(None, None, config_path=config)
    (settings,), options = fake_server.call_args
    assert (settings.vault_path, settings.draft_folder) == (vault.resolve(), "Inbox/AI")
    assert options["vault_settings"] == load_vault_settings(config)
    assert fake_server.ran == [True]


@pytest.mark.parametrize(
    ("vault", "draft_folder", "config", "message"),
    [
        (None, "Drafts", "present", "--config cannot be combined"),
        (None, None, "missing", "Trusted config not found"),
        ("missing", None, None, "Vault path must be an existing directory"),
    ],
    ids=["config-with-draft-folder", "missing-config", "missing-vault"],
)
def test_mcp_refuses_unusable_vault_options_before_starting(
    tmp_path, fake_server, vault, draft_folder, config, message
) -> None:
    present = tmp_path / "settings.toml"
    present.write_text("[vault]\n", encoding="utf-8")
    config_path = {"present": present, "missing": tmp_path / "missing.toml", None: None}[config]
    vault_path = tmp_path / vault if vault else None
    with pytest.raises(ValueError, match=message):
        cli._run_mcp(vault_path, draft_folder, config_path=config_path)
    fake_server.assert_not_called()


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


@pytest.mark.parametrize("root", ["relative/vault", "missing"], ids=["relative", "missing"])
def test_setup_refuses_a_vault_root_that_is_not_an_existing_absolute_directory(
    tmp_path, root
) -> None:
    config = tmp_path / "settings.toml"
    vault_root = root if root == "relative/vault" else str(tmp_path / root)
    with pytest.raises(ValueError, match="existing absolute directory"):
        cli._setup(config, vault_root=vault_root, allowed=("*",))
    assert not config.exists()


def test_setup_refuses_settings_the_reader_would_reject(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "settings.toml"
    with pytest.raises(ValueError, match="Unsafe allowed vault path"):
        cli._setup(config, vault_root=str(vault), allowed=("../outside",))
    assert not config.exists()


def test_cli_vault_search_and_path_read_use_scoped_configuration(tmp_path, cli_runner) -> None:
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

    search = cli_runner.ok("search-vault", "retry", "--config", str(config))
    assert [item["path"] for item in search["notes"]] == ["Notes/Retry.md"]
    assert "must not leak" not in json.dumps(search)

    note = cli_runner.ok(
        "read-vault-note",
        reference["path"],
        "--revision",
        reference["revision"],
        "--config",
        str(config),
    )
    assert note["path"] == "Notes/Retry.md"
    assert note["text"] == "A retry budget limits load."
    assert "note_id" not in note


def test_cli_create_draft_and_edit_suggestion_preserve_canonical_note(
    monkeypatch, tmp_path, cli_runner
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
    # This fake source has no transcript; full-read enforcement is tested elsewhere.
    monkeypatch.setattr("obsidian_ingest.responses.require_full_read", lambda *_: None)

    created = cli_runner.ok(
        "create-draft",
        metadata.source_id,
        "# Retry budget: why?\n\nA retry budget limits aggregate load.",
        "--config",
        str(config),
        "--related-note",
        target_path,
        "Explains retry policy.",
        "--read-token",
        "unchecked-for-this-fake-source",
    )
    assert Path(created["path"]).name == "retry-budget-why.md"
    contents = Path(created["path"]).read_text(encoding="utf-8")
    assert contents.startswith("# Retry budget: why?\n\nA retry budget limits aggregate load.")
    assert "## Related Notes\n\n- [[Notes/Retry.md]]: Explains retry policy." in contents

    edit = cli_runner.ok(
        "create-edit-suggestion",
        metadata.source_id,
        target_path,
        note["revision"],
        "Add aggregate retry budget",
        "The source adds a system-wide constraint.",
        "A retry budget caps aggregate retries.",
        "--config",
        str(config),
        "--read-token",
        "unchecked-for-this-fake-source",
    )
    assert Path(edit["path"]).name == "edit-retry.md"
    assert target.read_text(encoding="utf-8") == "Canonical note stays unchanged."


# --- Application behavior exercised through the CLI -----------------------------------


def _read_token(cli_runner, source_id: str, *db: str) -> str:
    """Read the whole transcript in order, as the skill requires, and return the token."""
    token = None
    offset = 0
    while True:
        extra = ("--read-token", token) if token else ()
        page = cli_runner.ok("read-source", source_id, "--offset", str(offset), *extra, *db)
        token = page["read_token"]
        if page["next_offset"] is None:
            return token
        offset = page["next_offset"]


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


def test_cli_source_operations_reuse_persistent_cache(tmp_path, fake_extraction, cli_runner):
    db = ("--cache-db", str(tmp_path / "cache.db"))
    first = cli_runner.ok("ingest-source", URL, *db)
    assert first["cached"] is False
    assert first["overview"]["exclusions"]["excluded_segments"] == 1
    again = cli_runner.ok("ingest-source", "https://youtu.be/video-123", *db)
    assert (again["source_id"], again["cached"]) == (first["source_id"], True)
    assert fake_extraction.call_count == 1
    source_id = first["source_id"]

    # Cache reuse is known only at ingest; a later read by handle reports it as unknown.
    assert first["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] is False
    assert again["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] is True
    overview = cli_runner.ok("get-source-overview", source_id, *db)
    assert overview["diagnostics"]["acquisition"]["reused_from_cache"] is None
    first["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] = None
    assert overview == first["overview"]
    assert cli_runner.ok("list-sections", source_id, *db) == [
        {
            "index": 0,
            "title": "Main idea",
            "path": "Main idea",
            "first_index": 0,
            "row_count": 3,
            "start": 0.0,
            "end": 20.0,
        }
    ]
    assert cli_runner.ok("list-exclusions", source_id, *db)[0]["source_indices"] == [3]

    texts = []
    offset = 0
    while offset is not None:
        page = cli_runner.ok("read-source", source_id, "--offset", str(offset), "--limit", "2", *db)
        texts.extend(segment[3] for segment in page["segments"])
        offset = page["next_offset"]
    assert texts == ["First atom", "Second atom at same time", "Third atom"]
    raw = cli_runner.ok("read-source", source_id, "--include-excluded", *db)
    assert raw["fields"] == ["source_index", "start", "duration", "text"]
    assert raw["segments"][-1] == [3, 2, 1, "Sponsor atom"]
    chapter = cli_runner.ok("read-section", source_id, "0", "--offset", "1", *db)
    assert [segment[3] for segment in chapter["segments"]] == [
        "Second atom at same time",
        "Third atom",
    ]

    refreshed = cli_runner.ok("ingest-source", URL, "--refresh", *db)
    assert (refreshed["source_id"], refreshed["cached"]) == (source_id, False)
    assert fake_extraction.call_count == 2


def test_cli_source_errors_use_shared_codes(tmp_path, fake_extraction, cli_runner):
    db = ("--cache-db", str(tmp_path / "cache.db"))
    source_id = cli_runner.ok("ingest-source", URL, *db)["source_id"]
    cases = [
        (("get-source-overview", "missing"), "SOURCE_NOT_FOUND", "Unknown source_id"),
        (("read-source", source_id, "--limit", "0"), "INVALID_ARGUMENT", "limit must be"),
        (("read-source", source_id, "--offset", "-1"), "INVALID_ARGUMENT", "offset must"),
        (
            ("read-source", source_id, "--start", "10", "--end", "1"),
            "INVALID_ARGUMENT",
            "end must not precede start",
        ),
        (("read-section", source_id, "4"), "INVALID_ARGUMENT", "section_index 4 out of range"),
    ]
    for args, code, message in cases:
        error = cli_runner.fail(*args, *db)
        assert error["code"] == code
        assert message in error["message"]

    error = cli_runner.fail("read-source", source_id, "--offset", "x", *db, exit_code=2)
    assert error["code"] == "INVALID_ARGUMENT"
    error = cli_runner.fail("list-sections", source_id, "extra", *db, exit_code=2)
    assert error["code"] == "INVALID_ARGUMENT"
    fake_extraction.side_effect = RuntimeError("network down")
    error = cli_runner.fail("ingest-source", URL, "--include-sponsors", *db)
    assert error["code"] == "INGEST_FAILED"


def test_cli_search_cursor_continues_across_invocations(tmp_path, cli_runner):
    vault = _vault_fixture(tmp_path)
    config = ("--config", str(_config(tmp_path, vault)))
    paths = []
    cursor = None
    while True:
        args = ["search-vault", "fixture", "--limit", "2", *config]
        if cursor:
            args += ["--cursor", cursor]
        page = cli_runner.ok(*args)
        assert all(
            set(note) == {"path", "title", "revision", "matched_terms", "snippet", "linkable"}
            for note in page["notes"]
        )
        paths.extend(note["path"] for note in page["notes"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert paths == [f"Notes/Fixture {index}.md" for index in range(5)]


def test_cli_search_cursor_is_untrusted_and_bound_to_query_and_scope(tmp_path, cli_runner):
    vault = _vault_fixture(tmp_path)
    config_path = _config(tmp_path, vault)
    config = ("--config", str(config_path))
    cursor = cli_runner.ok("search-vault", "fixture", "--limit", "2", *config)["next_cursor"]
    for args in (
        ("search-vault", "fixture", "--cursor", "not a cursor"),
        ("search-vault", "fixture", "--cursor", base64.urlsafe_b64encode(b"[1]").decode()),
        ("search-vault", "other", "--cursor", cursor),
        ("search-vault", "fixture", "--scope", "title", "--cursor", cursor),
    ):
        assert cli_runner.fail(*args, *config)["code"] == "INVALID_CURSOR"

    # Narrowing the trusted scope invalidates outstanding cursors.
    _config(tmp_path, vault, ("Private", "Notes/Fixture 4.md"))
    error = cli_runner.fail("search-vault", "fixture", "--cursor", cursor, *config)
    assert error["code"] == "INVALID_CURSOR"

    # A forged cursor that matches the current policy is only an offset: it never
    # surfaces excluded notes.
    with VaultReader(load_vault_settings(config_path)) as reader:
        forged = reader._cursor("fixture", "both", 0)
    page = cli_runner.ok("search-vault", "fixture", "--cursor", forged, *config)
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


def test_cli_note_reads_check_revisions_and_exclusions(tmp_path, cli_runner):
    vault = _vault_fixture(tmp_path)
    note = vault / "Notes/Fixture 0.md"
    note.write_text("0123456789abcdef", encoding="utf-8")
    config = ("--config", str(_config(tmp_path, vault)))
    first = cli_runner.ok("read-vault-note", "Notes/Fixture 0.md", "--limit", "10", *config)
    assert (first["text"], first["next_offset"]) == ("0123456789", 10)
    assert "note_id" not in first
    error = cli_runner.fail("read-vault-note", "Notes/Fixture 0.md", "--offset", "10", *config)
    assert error["code"] == "INVALID_ARGUMENT" and "revision is required" in error["message"]
    rest = cli_runner.ok(
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
    error = cli_runner.fail(
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
        error = cli_runner.fail("read-vault-note", path, *config)
        assert error == {"code": "NOTE_UNAVAILABLE", "message": "note is unavailable"}


def test_cli_missing_or_invalid_config_is_reported(tmp_path, cli_runner):
    error = cli_runner.fail("search-vault", "--config", str(tmp_path / "missing.toml"))
    assert error["code"] == "CONFIG_ERROR"
    invalid = tmp_path / "invalid.toml"
    invalid.write_text(f'[vault]\nroot = "{tmp_path}"\nallowed = ["../outside"]\n', "utf-8")
    error = cli_runner.fail("search-vault", "--config", str(invalid))
    assert error["code"] == "CONFIG_ERROR" and "vault.allowed entry" in error["message"]


def test_writes_require_a_read_token_covering_the_whole_transcript(
    tmp_path, fake_extraction, cli_runner
):
    vault = _vault_fixture(tmp_path)
    config = ("--config", str(_config(tmp_path, vault)))
    db = ("--cache-db", str(tmp_path / "cache.db"))
    source_id = cli_runner.ok("ingest-source", URL, *db)["source_id"]
    draft = ("create-draft", source_id, "# Idea\n\nBody.", *config, *db)

    first = cli_runner.ok("read-source", source_id, "--limit", "1", *db)
    assert first["read_token"].startswith("rt1-1-")
    error = cli_runner.fail(*draft, "--read-token", first["read_token"])
    assert error["code"] == "INCOMPLETE_READ" and "offset 1" in error["message"]

    # Skipping ahead serves the page but does not advance the token.
    skipped = cli_runner.ok(
        "read-source", source_id, "--offset", "2", "--read-token", first["read_token"], *db
    )
    assert skipped["read_token"] == first["read_token"]
    # A time-range read passes the token through unchanged.
    ranged = cli_runner.ok(
        "read-source",
        source_id,
        "--start",
        "0",
        "--end",
        "1",
        "--read-token",
        first["read_token"],
        *db,
    )
    assert ranged["read_token"] == first["read_token"]

    forged = first["read_token"][:-1] + ("0" if first["read_token"][-1] != "0" else "1")
    error = cli_runner.fail(*draft, "--read-token", forged)
    assert error["code"] == "INCOMPLETE_READ" and "source changed" in error["message"]
    assert not (vault / "00 Inbox/AI Drafts").exists() or not any(
        (vault / "00 Inbox/AI Drafts").iterdir()
    )

    token = _read_token(cli_runner, source_id, *db)
    unknown = cli_runner.fail(*draft, "--read-token", token, "--cite", "99")
    assert unknown["code"] == "INVALID_ARGUMENT" and "rows 0 to 3" in unknown["message"]
    excluded = cli_runner.fail(*draft, "--read-token", token, "--cite", "3")
    assert excluded["code"] == "INVALID_ARGUMENT" and "excluded" in excluded["message"]
    assert Path(cli_runner.ok(*draft, "--read-token", token)["path"]).exists()


def test_cli_publication_is_create_only_with_verified_links(
    monkeypatch, tmp_path, fake_extraction, cli_runner
):
    vault = _vault_fixture(tmp_path)
    target = vault / "Notes/Fixture 1.md"
    config = ("--config", str(_config(tmp_path, vault)))
    db = ("--cache-db", str(tmp_path / "cache.db"))
    source_id = cli_runner.ok("ingest-source", URL, *db)["source_id"]
    rt = ("--read-token", _read_token(cli_runner, source_id, *db))
    revision = cli_runner.ok("read-vault-note", "Notes/Fixture 1.md", *config)["revision"]
    canonical = {path: path.read_bytes() for path in vault.rglob("*.md")}

    # Ordinary related links need no revision and survive harmless human edits.
    target.write_text("edited by a human", encoding="utf-8")
    canonical[target] = target.read_bytes()
    draft = cli_runner.ok(
        "create-draft",
        source_id,
        "# Retry budget\n\nBody.",
        "--cite",
        "0",
        "--cite",
        "2",
        "--cite",
        "2",
        "--related-note",
        "Notes/Fixture 1.md",
        "Shared idea.",
        *config,
        *db,
        *rt,
    )
    text = Path(draft["path"]).read_text(encoding="utf-8")
    assert "- [[Notes/Fixture 1.md]]: Shared idea." in text
    # Rows 0 and 2 start within 30 s of each other, so one moment is listed.
    assert (
        "- Relevant timestamps: [00:00](https://www.youtube.com/watch?v=video-123&t=0s)\n" in text
    )
    assert Path(draft["path"]).name == "retry-budget.md"
    duplicate = cli_runner.ok(
        "create-draft", source_id, "# Retry budget\n\nBody.", *config, *db, *rt
    )
    assert Path(duplicate["path"]).name == "retry-budget-2.md"
    named = cli_runner.ok(
        "create-draft",
        source_id,
        "# Title: kept verbatim?\n\nBody.",
        "--filename",
        "../My_File.md",
        *config,
        *db,
        *rt,
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
        error = cli_runner.fail(*args, *config, *db, *rt)
        assert error["code"] == code
        assert "SECRET_MARKER" not in error["message"]
    assert sorted(drafts.iterdir()) == before

    current = cli_runner.ok("read-vault-note", "Notes/Fixture 1.md", *config)["revision"]
    edit = cli_runner.ok(
        "create-edit-suggestion",
        source_id,
        "Notes/Fixture 1.md",
        current,
        "Add detail",
        "The source adds context.",
        "New paragraph.",
        *config,
        *db,
        *rt,
    )
    assert Path(edit["path"]).name == "edit-fixture-1.md"
    assert Path(edit["path"]).read_text(encoding="utf-8").startswith("[[Notes/Fixture 1.md]]")
    assert {path: path.read_bytes() for path in canonical} == canonical
