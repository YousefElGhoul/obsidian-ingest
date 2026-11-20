"""Web Clipper notes: parsing, policy checks, cache precedence and citation invalidation."""

from pathlib import Path

import pytest
from articles import FakeWeb, Reply, article, json_ld, page, words

from obsidian_ingest.extractor.providers.article.clipper import (
    markdown_atoms,
    split_frontmatter,
)

URL = "https://example.com/posts/locked"

CLIP = """---
title: "Locked [[Secret]] | story #tag"
source: "https://example.com/posts/locked?utm_source=clipper"
author:
- "[[Ada Writer]]"
- "[[Bo Editor|Bo]]"
published: 2025-03-14
created: 2026-11-20
lang: en
tags:
  - "clippings"
unknown: ignored
---
Opening paragraph about retry budgets, with [a link](https://example.com/a). {opening}

## Why it matters

Retries amplify load during an outage. {why}

![Load chart](https://example.com/chart.png)

> Retries are a loan against future capacity.

- Cap retries per client
  - Even per endpoint

| Setting | Value |
| --- | --- |
| budget | 10% |

```python
def retry(n):
    return n
```

### Details

This post is sponsored by Example Corp.
"""


def clip_text(**overrides: str) -> str:
    text = CLIP.format(opening=words(40, "o"), why=words(40, "w"))
    for old, new in overrides.items():
        text = text.replace(old, new)
    return text


def test_frontmatter_reads_only_known_fields() -> None:
    fields, body = split_frontmatter(clip_text())
    assert fields["title"] == "Locked [[Secret]] | story #tag"
    assert fields["source"] == "https://example.com/posts/locked?utm_source=clipper"
    assert fields["author"] == ["Ada Writer", "Bo"]
    assert fields["published"] == "2025-03-14" and fields["lang"] == "en"
    assert "unknown" not in fields and "tags" not in fields
    assert body.startswith("Opening paragraph")
    assert split_frontmatter("no frontmatter") == ({}, "no frontmatter")
    assert split_frontmatter("---\nsource: x\nnever closed") == ({}, "---\nsource: x\nnever closed")


def test_markdown_body_becomes_kinds_and_sections() -> None:
    _, body = split_frontmatter(clip_text())
    atoms, sections = markdown_atoms(body)
    rows = [(a.kind, a.section, a.text) for a in atoms]
    assert rows[0][0:2] == ("paragraph", None)
    assert "[a link](https://example.com/a)" in rows[0][2]
    assert ("image", "Why it matters", "![Load chart](https://example.com/chart.png)") in rows
    assert ("quote", "Why it matters", "Retries are a loan against future capacity.") in rows
    assert ("list_item", "Why it matters", "  Even per endpoint") in rows
    assert ("table_row", "Why it matters", "Setting: budget; Value: 10%") in rows
    assert ("code", "Why it matters", "```python\ndef retry(n):\n    return n\n```") in rows
    assert sections == [("Why it matters", 1), ("Why it matters › Details", len(atoms) - 1)]


def test_clipper_keeps_only_http_link_and_image_destinations() -> None:
    atoms, _ = markdown_atoms(
        "[Read](https://example.com/a_(b)) or [help](relative/path).\n\n"
        '![Chart](<file:///local chart.png> "Title") and [action](obsidian://open).\n\n'
        '[Title [v2]](<https://example.com/a b> "Link title") or [Nested [label]](relative).\n\n'
        "`[example](relative/path)`"
    )
    assert [atom.text for atom in atoms] == [
        "[Read](https://example.com/a_%28b%29) or help.",
        "Chart and action.",
        '[Title [v2]](https://example.com/a%20b "Link title") or Nested [label].',
        "`[example](relative/path)`",
    ]


def test_clipper_preserves_heading_hashes_and_disambiguates_full_paths() -> None:
    long = "x" * 550
    atoms, sections = markdown_atoms(
        "## C#\nOne\n### Results (UK) ###\nTwo\n### Results (UK)\nThree\n"
        f"## {long}\nFour\n## {long}\nFive"
    )
    assert sections == [
        ("C#", 0),
        ("C# › Results (UK)", 1),
        ("C# › Results (UK) (2)", 2),
        ("x" * 500, 3),
        ("x" * 496 + " (2)", 4),
    ]
    assert [atom.section for atom in atoms] == [path for path, _ in sections]


def test_frontmatter_reads_indentless_author_list() -> None:
    fields, _ = split_frontmatter(
        '---\nauthor:\n- "[[Ada Writer]]"\n- "[[Bo Editor|Bo]]"\ntags:\n- clippings\n---\nBody'
    )
    assert fields == {"author": ["Ada Writer", "Bo"]}


@pytest.mark.parametrize(
    ("begin", "end", "kind"),
    [
        ("```\n", "\n```", "code"),
        ("> ", "", "quote"),
        ("- ", "", "list_item"),
        ("| ", " |", "table_row"),
    ],
)
def test_clipper_splits_all_oversized_block_kinds(begin: str, end: str, kind: str) -> None:
    text = "x" * 24_000
    atoms, _ = markdown_atoms(begin + text + end)
    assert len(atoms) > 1
    assert all(atom.kind == kind for atom in atoms)
    assert text in "".join(atom.text.removeprefix("(continued) ") for atom in atoms)


# --- End to end through the CLI -------------------------------------------------------


@pytest.fixture
def vault_setup(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Clippings").mkdir(parents=True)
    (vault / "Private").mkdir()
    config = tmp_path / "settings.toml"
    config.write_text(
        f'[vault]\nroot = "{vault}"\nallowed = ["*"]\nexcluded = ["Private"]\n', encoding="utf-8"
    )
    return vault, ("--config", str(config)), ("--cache-db", str(tmp_path / "cache.db"))


def _read_all(cli_runner, source_id: str, db) -> str:
    token, offset = None, 0
    while offset is not None:
        args = ("read-source", source_id, "--offset", str(offset), *db)
        page_data = cli_runner.ok(*args, *(("--read-token", token) if token else ()))
        token, offset = page_data["read_token"], page_data["next_offset"]
    return token


@pytest.mark.parametrize(
    ("path", "text", "code", "message"),
    [
        ("Private/clip.md", clip_text(), "NOTE_UNAVAILABLE", "unavailable"),
        ("Clippings/missing.md", None, "NOTE_UNAVAILABLE", "unavailable"),
        ("Clippings/plain.md", "# Just a note\n\nNo source.", "INVALID_ARGUMENT", "source URL"),
        (
            "Clippings/js.md",
            clip_text(
                **{"https://example.com/posts/locked?utm_source=clipper": "javascript:alert(1)"}
            ),
            "INVALID_ARGUMENT",
            "source URL",
        ),
        (
            "Clippings/file.md",
            clip_text(
                **{"https://example.com/posts/locked?utm_source=clipper": "file:///etc/passwd"}
            ),
            "INVALID_ARGUMENT",
            "source URL",
        ),
        (
            "Clippings/private.md",
            clip_text(
                **{"https://example.com/posts/locked?utm_source=clipper": "http://10.0.0.5/x"}
            ),
            "INGEST_FAILED",
            "private or local",
        ),
        ("Clippings/huge.md", clip_text() + "x " * 1_100_000, "INVALID_ARGUMENT", "at most"),
    ],
)
def test_notes_are_checked_before_ingesting(
    vault_setup, cli_runner, path: str, text: str | None, code: str, message: str
) -> None:
    vault, config, db = vault_setup
    if text is not None:
        (vault / path).write_text(text, encoding="utf-8")
    error = cli_runner.fail("ingest-source", "--note", path, *config, *db)
    assert error["code"] == code and message in error["message"]


def test_url_and_note_are_mutually_exclusive(vault_setup, cli_runner) -> None:
    _, config, db = vault_setup
    for args in ((), (URL, "--note", "Clippings/x.md")):
        error = cli_runner.fail("ingest-source", *args, *config, *db)
        assert error["code"] == "INVALID_ARGUMENT" and "exactly one" in error["message"]


def test_clip_precedence_and_citation_invalidation(vault_setup, monkeypatch, cli_runner) -> None:
    vault, config, db = vault_setup
    teaser = page(
        f"<article><h1>Locked</h1><p>{words(80, 'teaser')}.</p>"
        "<div class='paywall'>Subscribe to continue reading.</div></article>",
        head=json_ld(headline="Locked", isAccessibleForFree=False, wordCount=2000),
    )
    web = FakeWeb(routes={URL: Reply(teaser)}).install(monkeypatch)
    clip = vault / "Clippings/locked.md"
    clip.write_text(clip_text(), encoding="utf-8")
    note = ("--note", "Clippings/locked.md")

    # 1. The web page is paywalled: a clear failure that suggests clipping.
    failed = cli_runner.fail("ingest-source", URL, *config, *db)
    assert failed["code"] == "INGEST_FAILED" and "PAYWALL_SUSPECTED" in failed["message"]
    assert "Web Clipper" in failed["message"]

    # 2. The clip becomes the source's capture, keyed by its source URL.
    clipped = cli_runner.ok("ingest-source", *note, *config, *db)
    source_id = clipped["source_id"]
    evidence = clipped["overview"]["diagnostics"]["evidence"]
    assert clipped["cached"] is False and evidence["method"] == "web_clipper"
    assert evidence["note_path"] == "Clippings/locked.md"
    assert clipped["overview"]["details"]["authors"] == ["Ada Writer", "Bo"]
    excluded = cli_runner.ok("list-exclusions", source_id, *db)
    assert [item["reason"] for item in excluded] == ["sponsored"]

    # 3. The bare URL now returns the clip instead of failing again.
    fetches = len(web.requests)
    by_url = cli_runner.ok("ingest-source", URL, *config, *db)
    assert (by_url["source_id"], by_url["cached"]) == (source_id, True)
    assert by_url["overview"]["diagnostics"]["evidence"]["method"] == "web_clipper"
    assert len(web.requests) == fetches

    # 4. Re-ingesting the unchanged note reuses it.
    assert cli_runner.ok("ingest-source", *note, *config, *db)["cached"] is True

    # 5. Editing the note replaces the clip and invalidates earlier reads.
    token = _read_all(cli_runner, source_id, db)
    clip.write_text(clip_text(**{"Opening paragraph": "Rewritten opening"}), encoding="utf-8")
    replaced = cli_runner.ok("ingest-source", *note, *config, *db)
    assert (replaced["source_id"], replaced["cached"]) == (source_id, False)
    draft = ("create-draft", source_id, "# Note\n\nBody.", "--cite", "0", *config, *db)
    stale = cli_runner.fail(*draft, "--read-token", token)
    assert stale["code"] == "INCOMPLETE_READ" and "source changed" in stale["message"]

    # 6. A refresh that now succeeds on the web replaces the clip.
    clip_token = _read_all(cli_runner, source_id, db)
    web.routes[URL] = Reply(article(title="Locked"))
    refreshed = cli_runner.ok("ingest-source", URL, "--refresh", *config, *db)
    assert refreshed["source_id"] == source_id
    assert refreshed["overview"]["diagnostics"]["evidence"]["method"] == "extract"
    assert cli_runner.fail(*draft, "--read-token", clip_token)["code"] == "INCOMPLETE_READ"

    # 7. A refresh that fails keeps what is there (here, the clip again after re-clipping).
    cli_runner.ok("ingest-source", *note, *config, *db)
    web.routes[URL] = Reply("blocked", status=403)
    kept = cli_runner.fail("ingest-source", URL, "--refresh", *config, *db)
    assert kept["code"] == "INGEST_FAILED"
    overview = cli_runner.ok("get-source-overview", source_id, *db)
    assert overview["diagnostics"]["evidence"]["method"] == "web_clipper"

    # 8. A fresh read allows a draft cited by section, attributed from the clip.
    created = cli_runner.ok(
        *draft[:4], "1", *config, *db, "--read-token", _read_all(cli_runner, source_id, db)
    )
    text = Path(created["path"]).read_text(encoding="utf-8")
    assert "- Source: Locked \\[\\[Secret\\]\\] \\| story \\#tag\n" in text
    assert "- URL: https://example.com/posts/locked?utm_source=clipper\n" in text
    assert "- Authors: Ada Writer, Bo\n- Published: 2025-03-14\n- Language: en\n" in text
    assert text.endswith("- Relevant sections: Why it matters\n")
    assert clip.read_text(encoding="utf-8").startswith("---\ntitle:")  # never modified


def test_mcp_ingests_a_clip_by_note_id(vault_setup, monkeypatch, tmp_path) -> None:
    import asyncio

    from mcp.client import Client

    from obsidian_ingest import mcp_server
    from obsidian_ingest.config import load_vault_settings
    from obsidian_ingest.drafts import DraftWriterSettings

    vault, config, _ = vault_setup
    FakeWeb().install(monkeypatch)
    (vault / "Clippings/locked.md").write_text(clip_text(), encoding="utf-8")
    settings = load_vault_settings(Path(config[1]))
    server = mcp_server.create_server(
        DraftWriterSettings(settings.root, settings.draft_folder),
        cache_db=tmp_path / "mcp.db",
        vault_settings=settings,
    )

    async def run() -> None:
        async with Client(server) as client:
            found = await client.call_tool("search_vault", {"query": "retry budgets"})
            import json

            note_id = json.loads(found.content[0].text)["notes"][0]["note_id"]
            both = await client.call_tool("ingest_source", {"url": URL, "note_id": note_id})
            assert both.is_error and "exactly one" in both.content[0].text
            refresh = await client.call_tool("ingest_source", {"note_id": note_id, "refresh": True})
            assert refresh.is_error and "refresh applies only to url" in refresh.content[0].text
            result = (
                await client.call_tool("ingest_source", {"note_id": note_id})
            ).structured_content
            assert result["overview"]["diagnostics"]["evidence"]["method"] == "web_clipper"
            again = (
                await client.call_tool("ingest_source", {"note_id": note_id})
            ).structured_content
            assert (again["source_id"], again["cached"]) == (result["source_id"], True)

    asyncio.run(run())
