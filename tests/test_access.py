import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from articles import FakeWeb, Reply, article
from mcp.client import Client
from test_clipper import URL, clip_text
from test_mcp_server import _tool_data

from obsidian_ingest import cli, responses, source_cache
from obsidian_ingest.config import VaultSettings
from obsidian_ingest.drafts import DraftWriterSettings
from obsidian_ingest.extractor.providers.article.provider import ArticleProvider
from obsidian_ingest.mcp_server import create_server
from obsidian_ingest.notes import ClippedNote
from obsidian_ingest.vault import VaultReader

NOTE = "Clippings/locked.md"
MISSING_VAULT = "ingesting a note requires trusted vault settings"


def test_cli_rechecks_clip_access(tmp_path, monkeypatch, cli_runner):
    (tmp_path / NOTE).parent.mkdir()
    (tmp_path / NOTE).write_text(clip_text())
    config = tmp_path / "settings.toml"
    settings = f'[vault]\nroot = "{tmp_path}"\nallowed = ["*"]\n'
    config.write_text(settings)
    monkeypatch.setattr(cli, "default_config_path", lambda: config)
    db = ("--cache-db", str(tmp_path / "sources.db"))
    web = FakeWeb(routes={URL: Reply("blocked", status=403)}).install(monkeypatch)

    source_id = cli_runner.ok("ingest-source", "--note", NOTE, *db)["source_id"]
    assert cli_runner.ok("get-source-overview", source_id, *db)["title"].startswith("Locked")
    page = cli_runner.ok("read-source", source_id, *db)
    assert "Opening paragraph" in page["segments"][0][-1]
    assert cli_runner.ok("ingest-source", URL, *db)["cached"]
    assert not web.requests

    restricted = tmp_path / "restricted.toml"
    restricted.write_text(settings + 'excluded = ["Clippings"]\n')
    config_args = ("--config", str(restricted))
    token = ("--read-token", page["read_token"])
    for command, extra in [
        ("get-source-overview", ()),
        ("list-sections", ()),
        ("list-exclusions", ()),
        ("read-source", ()),
        ("read-section", ("0",)),
        ("get-thumbnail", ()),
        ("create-draft", ("# Draft\n\nBody", *token)),
        ("create-edit-suggestion", ("target.md", "revision", "change", "why", "body", *token)),
    ]:
        error = cli_runner.fail(command, source_id, *extra, *db, *config_args)
        assert error == {
            "code": "NOTE_UNAVAILABLE",
            "message": "this source comes from a clipped note the current settings cannot open",
        }
    failed = cli_runner.fail("ingest-source", URL, *db, *config_args)
    assert failed["code"] == "INGEST_FAILED" and "HTTP_403" in failed["message"]
    assert cli_runner.ok("ingest-source", URL, *db)["cached"]
    assert len(web.requests) == 1

    web.routes[URL] = Reply(article(title="Public version"))
    result = cli_runner.ok("ingest-source", URL, *db, *config_args)
    assert not result["cached"] and result["source_id"] == source_id
    assert result["overview"]["title"] == "Public version"
    assert cli_runner.ok("get-source-overview", source_id, *db)["title"] == "Public version"


def test_mcp_without_vault_cannot_reuse_clip(tmp_path, monkeypatch):
    (tmp_path / NOTE).parent.mkdir()
    (tmp_path / NOTE).write_text(clip_text())
    web = FakeWeb(routes={URL: Reply("blocked", status=403)}).install(monkeypatch)
    writer = DraftWriterSettings(tmp_path)
    database = tmp_path / "sources.db"
    trusted = create_server(
        writer, cache_db=database, vault_settings=VaultSettings(tmp_path, ("*",), ())
    )
    write_only = create_server(writer, cache_db=database)

    async def run():
        async with Client(trusted) as client:
            search = await client.call_tool("search_vault", {"query": "retry budgets"})
            note_id = _tool_data(search)["notes"][0]["note_id"]
            ingest = await client.call_tool("ingest_source", {"note_id": note_id})
            source_id = _tool_data(ingest)["source_id"]
            read = await client.call_tool("read_source", {"source_id": source_id})
            assert "Opening paragraph" in _tool_data(read)["segments"][0][-1]
        async with Client(write_only) as client:
            read = await client.call_tool("read_source", {"source_id": source_id})
            assert read.is_error and "NOTE_UNAVAILABLE" in read.content[0].text
            via_url = await client.call_tool("ingest_source", {"url": URL})
            assert via_url.is_error and "HTTP_403" in via_url.content[0].text
            note = await client.call_tool("ingest_source", {"note_id": note_id})
            assert note.is_error and f"NOTE_UNAVAILABLE: {MISSING_VAULT}" in note.content[0].text
        assert len(web.requests) == 1

    asyncio.run(run())


def test_url_ingest_does_not_reuse_clip_published_during_fetch(tmp_path, monkeypatch):
    FakeWeb(routes={URL: Reply(article(title="Public version"))}).install(monkeypatch)
    database = tmp_path / "sources.db"
    acquire = source_cache.ingest_source

    def interleave(url: str, *, exclude_sponsors: bool):
        cache = source_cache.SourceCache(database)
        clip = cache.ingest_note(ClippedNote(NOTE, "revision", clip_text()))
        assert not clip.cached
        return acquire(url, exclude_sponsors=exclude_sponsors)

    monkeypatch.setattr(source_cache, "ingest_source", interleave)
    result = responses.ingest(source_cache.SourceCache(database), URL)
    assert not result["cached"]
    assert result["overview"]["title"] == "Public version"


def test_cli_note_ingest_without_vault(tmp_path, monkeypatch, cli_runner):
    config = tmp_path / "missing.toml"
    monkeypatch.setattr(cli, "default_config_path", lambda: config)
    for args in ((), ("--config", str(config))):
        error = cli_runner.fail("ingest-source", "--note", NOTE, *args)
        assert error == {"code": "NOTE_UNAVAILABLE", "message": MISSING_VAULT}


def test_a_clip_is_not_served_to_another_vault_with_the_same_path(
    tmp_path, monkeypatch, cli_runner
):
    db = ("--cache-db", str(tmp_path / "sources.db"))
    FakeWeb(routes={URL: Reply("blocked", status=403)}).install(monkeypatch)
    configs = []
    for name in ("vault-a", "vault-b"):
        root = tmp_path / name
        (root / NOTE).parent.mkdir(parents=True)
        (root / NOTE).write_text(clip_text())
        config = tmp_path / f"{name}.toml"
        config.write_text(f'[vault]\nroot = "{root}"\nallowed = ["*"]\n')
        configs.append(("--config", str(config)))
    source_id = cli_runner.ok("ingest-source", "--note", NOTE, *db, *configs[0])["source_id"]
    # Vault B has a note at the same relative path, but it is not the clip's vault.
    error = cli_runner.fail("get-source-overview", source_id, *db, *configs[1])
    assert error["code"] == "NOTE_UNAVAILABLE"
    assert cli_runner.ok("get-source-overview", source_id, *db, *configs[0])["title"]


def test_cached_clip_is_refused_after_vault_replacement(tmp_path, cli_runner):
    root = tmp_path / "vault"
    (root / NOTE).parent.mkdir(parents=True)
    (root / NOTE).write_text(clip_text())
    config = tmp_path / "settings.toml"
    config.write_text(f'[vault]\nroot = "{root}"\nallowed = ["*"]\n')
    options = ("--config", str(config), "--cache-db", str(tmp_path / "sources.db"))
    source_id = cli_runner.ok("ingest-source", "--note", NOTE, *options)["source_id"]

    root.rename(tmp_path / "old-vault")
    (root / NOTE).parent.mkdir(parents=True)
    (root / NOTE).write_text(clip_text())
    assert cli_runner.fail("read-source", source_id, *options)["code"] == "NOTE_UNAVAILABLE"


@pytest.mark.parametrize("had_vault_id", [False, True])
def test_cached_clip_with_legacy_vault_identity_is_refused(tmp_path, monkeypatch, had_vault_id):
    (tmp_path / NOTE).parent.mkdir()
    (tmp_path / NOTE).write_text(clip_text())
    cache = source_cache.SourceCache(tmp_path / "sources.db")
    normalize = source_cache.source_from_content

    def with_legacy_evidence(content):
        source = normalize(content)
        evidence = dict(source.acquisition.evidence)
        evidence.pop("note_vault")
        if had_vault_id:
            evidence["note_vault"] = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:16]
        return replace(source, acquisition=replace(source.acquisition, evidence=evidence))

    monkeypatch.setattr(source_cache, "source_from_content", with_legacy_evidence)
    with VaultReader(VaultSettings(tmp_path, ("*",), ())) as vault:
        ref = vault.get_path_reference(NOTE)
        source_id = responses.ingest_note(cache, vault, ref.note_id)["source_id"]
        with pytest.raises(responses.OperationError, match="NOTE_UNAVAILABLE"):
            responses.load_source(cache, source_id, vault)


def test_clip_access_does_not_scan_or_register_backing_note(tmp_path, monkeypatch):
    (tmp_path / NOTE).parent.mkdir()
    (tmp_path / NOTE).write_text(clip_text())
    cache = source_cache.SourceCache(tmp_path / "sources.db")
    with VaultReader(VaultSettings(tmp_path, ("*",), ())) as vault:
        ref = vault.get_path_reference(NOTE)
        source_id = responses.ingest_note(cache, vault, ref.note_id)["source_id"]

        def unexpected(*args, **kwargs):
            pytest.fail("checking clip access should only open and close the note")

        monkeypatch.setattr(vault, "_scan_file", unexpected)
        monkeypatch.setattr(vault, "_register", unexpected)
        assert responses.load_source(cache, source_id, vault).transcript.segments
        assert responses.ingest(cache, URL, vault=vault)["cached"]
        (tmp_path / NOTE).unlink()
        with pytest.raises(responses.OperationError, match="NOTE_UNAVAILABLE"):
            responses.load_source(cache, source_id, vault)


def test_unchanged_clip_skips_extraction(tmp_path, monkeypatch):
    cache = source_cache.SourceCache(tmp_path / "sources.db")
    note = ClippedNote(NOTE, "revision", clip_text(), "vault-a")
    first = cache.ingest_note(note)

    def unexpected(*args, **kwargs):
        pytest.fail("an unchanged clip should not be extracted again")

    monkeypatch.setattr(ArticleProvider, "acquire_note", unexpected)
    second = cache.ingest_note(note)
    assert second.cached
    assert second.source_id == first.source_id


def test_skill_does_not_retry_unconfirmed_writes():
    skill = Path(cli.__file__).resolve().parents[2] / "SKILL.md"
    assert "report it as unconfirmed and do not retry" in " ".join(skill.read_text().split())
