"""Canonical identity: URL variants share one capture, which no other page can claim."""

import sqlite3
from pathlib import Path

import pytest
from articles import FakeWeb, Reply, page, words

from obsidian_ingest import source_cache

CANONICAL = "https://example.com/story"
AMP = "https://example.com/story/amp"
MOBILE = "https://m.example.com/story"


def story(title: str, canonical: str | None = CANONICAL, text: str = "story") -> str:
    link = f"<link rel='canonical' href='{canonical}'>" if canonical else ""
    body = "".join(f"<p>{words(40, f'{text}{i}w')}.</p>" for i in range(4))
    return page(f"<article><h1>{title}</h1>{body}</article>", head=f"<title>{title}</title>{link}")


@pytest.fixture
def db(tmp_path):
    return ("--cache-db", str(tmp_path / "sources.db"))


def test_variants_share_the_canonical_capture(monkeypatch, cli_runner, db) -> None:
    web = FakeWeb(
        routes={
            AMP: Reply(story("Story (AMP)")),
            MOBILE: Reply(story("Story (mobile)")),
            CANONICAL: Reply(story("Story")),
        }
    ).install(monkeypatch)
    first = cli_runner.ok("ingest-source", AMP, *db)
    assert first["overview"]["canonical_url"] == CANONICAL
    # The canonical page itself is fetched once, since the AMP capture is not its own,
    # and keeps the handle the AMP request created.
    direct = cli_runner.ok("ingest-source", CANONICAL, *db)
    assert direct["source_id"] == first["source_id"] and not direct["cached"]
    assert direct["overview"]["title"] == "Story"
    # Every variant now reuses the canonical page's capture.
    for url in (AMP, CANONICAL):
        again = cli_runner.ok("ingest-source", url, *db)
        assert again["cached"] and again["source_id"] == first["source_id"]
    mobile = cli_runner.ok("ingest-source", MOBILE, *db)
    assert mobile["source_id"] == first["source_id"]
    assert [url for url, _ in web.requests] == [AMP, CANONICAL, MOBILE]
    assert cli_runner.ok("ingest-source", MOBILE, *db)["cached"]


def test_a_page_cannot_claim_another_pages_capture(monkeypatch, cli_runner, db) -> None:
    impostor = "https://example.com/users/eve/post"
    web = FakeWeb(
        routes={
            impostor: Reply(story("Eve's copy", text="eve")),
            CANONICAL: Reply(story("Story")),
        }
    ).install(monkeypatch)
    claimed = cli_runner.ok("ingest-source", impostor, *db)
    genuine = cli_runner.ok("ingest-source", CANONICAL, *db)
    assert genuine["overview"]["title"] == "Story" and not genuine["cached"]
    assert genuine["source_id"] == claimed["source_id"]
    page_rows = cli_runner.ok("read-source", genuine["source_id"], *db)["segments"]
    assert all("eve" not in row[-1] for row in page_rows)
    assert len(web.requests) == 2


def test_a_cross_site_canonical_is_ignored(monkeypatch, cli_runner, db) -> None:
    syndicated = "https://news.example.org/story"
    web = FakeWeb(
        routes={
            syndicated: Reply(story("Syndicated", canonical=CANONICAL)),
            CANONICAL: Reply(story("Story")),
        }
    ).install(monkeypatch)
    copy = cli_runner.ok("ingest-source", syndicated, *db)
    original = cli_runner.ok("ingest-source", CANONICAL, *db)
    assert copy["source_id"] != original["source_id"]
    assert cli_runner.ok("ingest-source", syndicated, *db)["cached"]
    assert len(web.requests) == 2


def test_an_alias_survives_refresh_and_follows_a_new_canonical(monkeypatch, cli_runner, db) -> None:
    web = FakeWeb(routes={AMP: Reply(story("Story (AMP)"))}).install(monkeypatch)
    first = cli_runner.ok("ingest-source", AMP, *db)
    refreshed = cli_runner.ok("ingest-source", AMP, "--refresh", *db)
    assert refreshed["source_id"] == first["source_id"]
    assert cli_runner.ok("ingest-source", AMP, *db)["cached"]
    # Once the page stops naming a canonical URL, the request owns its own capture.
    web.routes[AMP] = Reply(story("Standalone", canonical=None))
    alone = cli_runner.ok("ingest-source", AMP, "--refresh", *db)
    assert alone["source_id"] != first["source_id"]
    assert cli_runner.ok("ingest-source", AMP, *db)["source_id"] == alone["source_id"]


def test_identical_text_is_reported_not_merged(monkeypatch, cli_runner, db) -> None:
    one, two = "https://example.com/a", "https://example.net/a"
    FakeWeb(
        routes={one: Reply(story("A", canonical=None)), two: Reply(story("A", canonical=None))}
    ).install(monkeypatch)
    first = cli_runner.ok("ingest-source", one, *db)
    second = cli_runner.ok("ingest-source", two, *db)
    assert first["source_id"] != second["source_id"]
    assert second["overview"]["diagnostics"]["same_content_as"] == [first["source_id"]]
    overview = cli_runner.ok("get-source-overview", first["source_id"], *db)
    assert overview["diagnostics"]["same_content_as"] == [second["source_id"]]


def test_a_schema_3_cache_must_be_reset(tmp_path) -> None:
    path = tmp_path / "sources.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 3")
    with pytest.raises(source_cache.SourceCacheError, match="schema version 3"):
        source_cache.SourceCache(path)


# --- Conditional refresh -----------------------------------------------------------------

PAGE = "https://example.com/essay"


def essay(title: str = "Essay") -> str:
    return story(title, canonical=None)


def test_unchanged_page_keeps_its_capture_and_read_tokens(
    tmp_path, monkeypatch, cli_runner, db
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "settings.toml"
    config.write_text(f'[vault]\nroot = "{vault}"\nallowed = ["*"]\n')
    options = (*db, "--config", str(config))
    validators = {"ETag": '"v1"', "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
    web = FakeWeb(routes={PAGE: Reply(essay(), headers=validators)}).install(monkeypatch)
    first = cli_runner.ok("ingest-source", PAGE, *options)
    assert "If-None-Match" not in web.sent_headers[0]
    assert "refreshed" not in first
    read = cli_runner.ok("read-source", first["source_id"], "--limit", "200", *options)
    assert read["next_offset"] is None

    web.routes[PAGE] = Reply(status=304)
    again = cli_runner.ok("ingest-source", PAGE, "--refresh", *options)
    assert again["refreshed"] == "not_modified" and again["source_id"] == first["source_id"]
    assert web.sent_headers[1]["If-None-Match"] == '"v1"'
    assert web.sent_headers[1]["If-Modified-Since"] == validators["Last-Modified"]
    acquired = again["overview"]["diagnostics"]["acquisition"]["acquired_at"]
    assert acquired == first["overview"]["diagnostics"]["acquisition"]["acquired_at"]
    # The capture is untouched, so a read finished before the refresh still authorizes.
    draft = cli_runner.ok(
        "create-draft", first["source_id"], "# Note\n\nBody", "--read-token", read["read_token"],
        "--config", str(config), *db,
    )  # fmt: skip
    assert Path(draft["path"]).is_file()


def test_changed_page_replaces_the_capture(monkeypatch, cli_runner, db) -> None:
    web = FakeWeb(routes={PAGE: Reply(essay(), headers={"ETag": '"v1"'})}).install(monkeypatch)
    first = cli_runner.ok("ingest-source", PAGE, *db)
    web.routes[PAGE] = Reply(essay("Essay, revised"), headers={"ETag": '"v2"'})
    again = cli_runner.ok("ingest-source", PAGE, "--refresh", *db)
    assert again["refreshed"] == "replaced" and again["source_id"] == first["source_id"]
    assert again["overview"]["title"] == "Essay, revised"
    assert again["overview"]["diagnostics"]["evidence"]["http"]["etag"] == '"v2"'


def test_unsolicited_304_is_an_error(monkeypatch, cli_runner, db) -> None:
    FakeWeb(routes={PAGE: Reply(status=304)}).install(monkeypatch)
    error = cli_runner.fail("ingest-source", PAGE, *db)
    assert error["code"] == "INGEST_FAILED" and "HTTP_304" in error["message"]


def test_unsafe_validators_are_not_stored_or_sent(monkeypatch, cli_runner, db) -> None:
    web = FakeWeb(routes={PAGE: Reply(essay(), headers={"ETag": "x" * 300})}).install(monkeypatch)
    first = cli_runner.ok("ingest-source", PAGE, *db)
    assert first["overview"]["diagnostics"]["evidence"]["http"]["etag"] is None
    cli_runner.ok("ingest-source", PAGE, "--refresh", *db)
    assert "If-None-Match" not in web.sent_headers[1]
