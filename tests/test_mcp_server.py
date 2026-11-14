import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client

from obsidian_ingest import cli, mcp_server, responses, source_cache
from obsidian_ingest.config import VaultSettings
from obsidian_ingest.drafts import DraftWriterSettings
from obsidian_ingest.extractor import Provider
from obsidian_ingest.extractor.metadata import (
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
)
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.source import Source

URL = "https://www.youtube.com/watch?v=video-123"


def _sample_source() -> Source:
    metadata = normalize_youtube_metadata(
        {
            "id": "video-123",
            "title": "A useful explanation",
            "description": "Context for the topic",
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
            TranscriptSegment("Second atom at same time", 1, 0.5),
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


def _tool_data(result) -> dict:
    assert not result.is_error
    data = result.structured_content
    if data is None:
        text = next(item.text for item in result.content if getattr(item, "text", None))
        data = json.loads(text)
    return data.get("result", data) if len(data) == 1 and "result" in data else data


def test_stdio_server_exposes_source_navigation_and_bound_draft_writer(
    monkeypatch, tmp_path: Path
) -> None:
    source = _sample_source()
    ingest = MagicMock(return_value=source)
    monkeypatch.setattr(source_cache, "ingest_source", ingest)
    vault = tmp_path / "vault"
    vault.mkdir()
    server = mcp_server.create_server(DraftWriterSettings(vault), cache_db=tmp_path / "cache.db")

    async def exercise_tools() -> None:
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert names == {
                "ingest_source",
                "get_source_overview",
                "list_chapters",
                "list_exclusions",
                "read_transcript",
                "read_chapter",
                "create_draft",
            }
            draft_tool = next(tool for tool in tools.tools if tool.name == "create_draft")
            assert set(draft_tool.input_schema["properties"]) == {
                "source_id",
                "contents",
                "filename",
                "timestamps",
                "related_notes",
            }
            assert set(draft_tool.input_schema["required"]) == {"source_id", "contents"}
            assert "H1" in draft_tool.description
            related_schema = draft_tool.input_schema["properties"]["related_notes"]
            assert related_schema["anyOf"][0]["items"]["$ref"] == "#/$defs/RelatedNoteInput"
            related_item_schema = draft_tool.input_schema["$defs"]["RelatedNoteInput"]
            assert set(related_item_schema["required"]) == {"note_id", "reason"}
            assert "revision" in related_item_schema["properties"]
            transcript_tool = next(tool for tool in tools.tools if tool.name == "read_transcript")
            assert transcript_tool.input_schema["properties"]["limit"]["default"] == 100

            ingested = _tool_data(await client.call_tool("ingest_source", {"url": URL}))
            source_id = ingested["source_id"]
            assert ingested["cached"] is False
            hit = _tool_data(await client.call_tool("ingest_source", {"url": URL}))
            assert hit["source_id"] == source_id
            assert hit["cached"] is True
            assert ingested["overview"]["title"] == "A useful explanation"
            assert ingested["overview"]["provider"] == Provider.YOUTUBE.value
            assert ingested["overview"]["chapter_count"] == 1
            assert ingested["overview"]["sponsor_filtering"] == {
                "status": "complete",
                "enabled": True,
                "categories": ["sponsor"],
                "error": None,
                "original_segments": 4,
                "retained_segments": 3,
                "excluded_segments": 1,
            }

            overview = _tool_data(
                await client.call_tool("get_source_overview", {"source_id": source_id})
            )
            assert overview["description"] == "Context for the topic"

            chapters = _tool_data(await client.call_tool("list_chapters", {"source_id": source_id}))
            assert chapters == [{"index": 0, "title": "Main idea", "start": 0.0, "end": 20.0}]
            exclusions = _tool_data(
                await client.call_tool("list_exclusions", {"source_id": source_id})
            )
            assert exclusions == [
                {
                    "range_index": 0,
                    "start": 2,
                    "end": 3,
                    "reason": "sponsor",
                    "provenance": "SponsorBlock",
                    "source_indices": [3],
                }
            ]

            first_page = _tool_data(
                await client.call_tool(
                    "read_transcript", {"source_id": source_id, "offset": 0, "limit": 2}
                )
            )
            page_result = await client.call_tool(
                "read_transcript", {"source_id": source_id, "offset": 0, "limit": 2}
            )
            assert page_result.structured_content is None
            assert len(page_result.content) == 1
            assert page_result.content[0].text.count("First atom") == 1
            assert (
                len(
                    json.dumps(
                        {"content": [{"type": "text", "text": page_result.content[0].text}]},
                        separators=(",", ":"),
                    ).encode()
                )
                < mcp_server.MAX_PAGE_ENVELOPE_BYTES
            )
            assert first_page["limit"] == 2
            assert [segment[3] for segment in first_page["segments"]] == [
                "First atom",
                "Second atom at same time",
            ]
            assert first_page["next_offset"] == 2
            second_page = _tool_data(
                await client.call_tool(
                    "read_transcript", {"source_id": source_id, "offset": 2, "limit": 2}
                )
            )
            assert [segment[3] for segment in second_page["segments"]] == ["Third atom"]
            assert second_page["next_offset"] is None
            raw_page = _tool_data(
                await client.call_tool(
                    "read_transcript",
                    {"source_id": source_id, "include_excluded": True, "offset": 3},
                )
            )
            assert [segment[3] for segment in raw_page["segments"]] == ["Sponsor atom"]
            assert raw_page["segments"][0][0] == 3

            chapter_page = _tool_data(
                await client.call_tool(
                    "read_chapter",
                    {"source_id": source_id, "chapter_index": 0, "limit": 2, "offset": 1},
                )
            )
            assert [segment[3] for segment in chapter_page["segments"]] == [
                "Second atom at same time",
                "Third atom",
            ]
            invalid_chapter = await client.call_tool(
                "read_chapter", {"source_id": source_id, "chapter_index": 4}
            )
            assert invalid_chapter.is_error
            assert "chapter_index 4 out of range" in invalid_chapter.content[0].text

            invalid_range = await client.call_tool(
                "read_transcript", {"source_id": source_id, "start": 10, "end": 1}
            )
            assert invalid_range.is_error
            assert "end must not precede start" in invalid_range.content[0].text

            draft = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {
                        "source_id": source_id,
                        "contents": "# A useful note: why?\n\nAgent-authored content.",
                        "timestamps": [1.0],
                    },
                )
            )
            draft_path = Path(draft["path"])
            assert draft_path == vault / "00 Inbox/AI Drafts/a-useful-note-why.md"
            draft_contents = draft_path.read_text(encoding="utf-8")
            assert draft_contents.startswith(
                "# A useful note: why?\n\nAgent-authored content.\n\n## Source"
            )
            assert "https://www.youtube.com/watch?v=video-123" in draft_contents
            assert "1s" in draft_contents

            duplicate = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {"source_id": source_id, "contents": "# A useful note: why?\n\nAgain."},
                )
            )
            assert Path(duplicate["path"]).name == "a-useful-note-why-2.md"

            named = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {
                        "source_id": source_id,
                        "contents": "# Title: kept verbatim?\n\nBody.",
                        "filename": "../My_File.md",
                    },
                )
            )
            assert Path(named["path"]) == vault / "00 Inbox/AI Drafts/my-file.md"
            assert Path(named["path"]).read_text(encoding="utf-8").startswith("# Title: kept")

            before = sorted((vault / "00 Inbox/AI Drafts").iterdir())
            untitled = await client.call_tool(
                "create_draft", {"source_id": source_id, "contents": "Body without a title."}
            )
            assert untitled.is_error
            assert "INVALID_ARGUMENT: contents must start with an H1" in untitled.content[0].text
            assert sorted((vault / "00 Inbox/AI Drafts").iterdir()) == before

            invalid_draft = await client.call_tool(
                "create_draft",
                {
                    "source_id": source_id,
                    "contents": "# Invalid formatting\n\n## Sources\nAgent list\n\n## Notes\nBody",
                },
            )
            assert invalid_draft.is_error
            assert "must not include a Source" in invalid_draft.content[0].text
            assert not (vault / "00 Inbox/AI Drafts/invalid-formatting.md").exists()

            referenced_draft = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {
                        "source_id": source_id,
                        "contents": (
                            "# References included\n\n"
                            "Body.\n\n## References\n- [Paper](https://example.com)"
                        ),
                    },
                )
            )
            reference_text = Path(referenced_draft["path"]).read_text(encoding="utf-8")
            assert reference_text.count("## Source") == 1
            assert "## References\n- [Paper](https://example.com)\n\n## Source" in reference_text

            missing = await client.call_tool("read_transcript", {"source_id": "not-ingested"})
            assert missing.is_error
            assert "SOURCE_NOT_FOUND: Unknown source_id" in missing.content[0].text

            # Above the maximum is capped, not rejected; the page reports what was applied.
            capped = await client.call_tool(
                "read_transcript", {"source_id": source_id, "limit": 500}
            )
            assert not capped.is_error
            assert json.loads(capped.content[0].text)["limit"] == 200
            invalid_page = await client.call_tool(
                "read_transcript", {"source_id": source_id, "limit": 0}
            )
            assert invalid_page.is_error
            assert "INVALID_ARGUMENT: limit" in invalid_page.content[0].text

    asyncio.run(exercise_tools())
    ingest.assert_called_once_with(URL, exclude_sponsors=True)


def test_source_handles_survive_server_restarts(tmp_path: Path, monkeypatch) -> None:
    source = _sample_source()
    monkeypatch.setattr(
        source_cache,
        "ingest_source",
        lambda url, exclude_sponsors=True: source,
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    database = tmp_path / "cache.db"
    server = mcp_server.create_server(DraftWriterSettings(vault), cache_db=database)

    async def ingest_and_read() -> str:
        async with Client(server) as client:
            result = _tool_data(await client.call_tool("ingest_source", {"url": URL}))
            return result["source_id"]

    source_id = asyncio.run(ingest_and_read())
    second_server = mcp_server.create_server(DraftWriterSettings(vault), cache_db=database)
    extraction = MagicMock(side_effect=AssertionError("Restart must not extract"))
    monkeypatch.setattr(source_cache, "ingest_source", extraction)

    async def read_from_new_server():
        async with Client(second_server) as client:
            overview = _tool_data(
                await client.call_tool("get_source_overview", {"source_id": source_id})
            )
            assert overview["title"] == source.metadata.title
            assert _tool_data(await client.call_tool("list_chapters", {"source_id": source_id}))
            assert _tool_data(await client.call_tool("list_exclusions", {"source_id": source_id}))
            transcript = _tool_data(
                await client.call_tool("read_transcript", {"source_id": source_id})
            )
            assert transcript["total_segments"] == 3
            chapter = _tool_data(
                await client.call_tool("read_chapter", {"source_id": source_id, "chapter_index": 0})
            )
            assert chapter["segments"] == transcript["segments"]
            draft = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {
                        "source_id": source_id,
                        "contents": "# After restart\n\nUseful content.",
                    },
                )
            )
            assert Path(draft["path"]).is_file()
            hit = _tool_data(
                await client.call_tool("ingest_source", {"url": "https://youtu.be/video-123"})
            )
            assert hit["cached"] is True
            assert hit["source_id"] == source_id

    asyncio.run(read_from_new_server())
    extraction.assert_not_called()


def test_long_transcript_pages_are_bounded_and_preserve_every_atom(tmp_path, monkeypatch):
    from dataclasses import replace

    source = _sample_source()
    segments = tuple(
        TranscriptSegment(f"Atom {index} – multilingual 文本", float(index), 0.25)
        for index in range(180)
    )
    transcript = replace(source.transcript, segments=segments)
    metadata = replace(
        source.metadata, chapters=(type(source.metadata.chapters[0])("All", 0, 180),)
    )
    source = replace(
        source,
        metadata=metadata,
        transcript=transcript,
        filtered_transcript=filter_source_transcript(metadata, transcript, source.exclusion_lookup),
    )
    monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: source)
    vault = tmp_path / "vault"
    vault.mkdir()
    server = mcp_server.create_server(DraftWriterSettings(vault), cache_db=tmp_path / "cache.db")

    async def read_pages(tool: str) -> list[dict]:
        async with Client(server) as client:
            source_id = _tool_data(await client.call_tool("ingest_source", {"url": URL}))[
                "source_id"
            ]
            pages = []
            offset = 0
            while True:
                result = await client.call_tool(
                    tool,
                    (
                        {"source_id": source_id, "offset": offset, "include_excluded": True}
                        if tool == "read_transcript"
                        else {
                            "source_id": source_id,
                            "chapter_index": 0,
                            "offset": offset,
                            "include_excluded": True,
                        }
                    ),
                )
                page = _tool_data(result)
                envelope = json.dumps(
                    {"content": [{"type": "text", "text": result.content[0].text}]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
                assert len(envelope) <= mcp_server.MAX_PAGE_ENVELOPE_BYTES
                assert len(page["segments"]) <= mcp_server.MAX_PAGE_SIZE
                pages.append(page)
                if page["next_offset"] is None:
                    break
                assert page["next_offset"] == offset + len(page["segments"])
                offset = page["next_offset"]
            return pages

    for tool in ("read_transcript", "read_chapter"):
        pages = asyncio.run(read_pages(tool))
        actual = [segment for page in pages for segment in page["segments"]]
        assert [segment[3] for segment in actual] == [segment.text for segment in segments]
        assert [segment[0] for segment in actual] == list(range(180))
        assert all(page["total_segments"] == 180 for page in pages)
        assert all(page["limit"] == mcp_server.DEFAULT_PAGE_SIZE for page in pages)
        assert [page["remaining_segments"] for page in pages][-1] == 0
        assert all(
            page["remaining_segments"] == 180 - page["offset"] - len(page["segments"])
            for page in pages
        )


def test_single_oversized_atom_fails_without_truncation():
    segment = TranscriptSegment("x" * mcp_server.MAX_PAGE_ENVELOPE_BYTES, 0, 1)
    with pytest.raises(ValueError, match="segment 7 exceeds"):
        responses.segment_page(((7, segment),), 0, 40)


def test_subprocess_restart_preserves_cached_source_and_draft(tmp_path, monkeypatch):
    source = _sample_source()
    database = tmp_path / "persistent-cache.db"
    cache = source_cache.SourceCache(database)
    monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: source)
    seeded = cache.ingest(URL)
    monkeypatch.undo()

    vault = tmp_path / "vault"
    vault.mkdir()
    code = (
        "import sys; from pathlib import Path; from obsidian_ingest.cli import _run_mcp; "
        "_run_mcp(Path(sys.argv[1]), 'Drafts', Path(sys.argv[2]))"
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-c", code, str(vault), str(database)],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "skills/obsidian-ingest/src"),
        },
    )

    async def run_server() -> None:
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as client,
        ):
            await client.initialize()
            overview = _tool_data(
                await client.call_tool("get_source_overview", {"source_id": seeded.source_id})
            )
            assert overview["title"] == source.metadata.title
            ingested = _tool_data(
                await client.call_tool("ingest_source", {"url": "https://youtu.be/video-123"})
            )
            assert ingested["cached"] is True
            assert ingested["source_id"] == seeded.source_id
            page = _tool_data(
                await client.call_tool("read_transcript", {"source_id": seeded.source_id})
            )
            assert page["segments"][0][3] == "First atom"
            result = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {
                        "source_id": seeded.source_id,
                        "contents": "# Restarted source\n\nA useful body.",
                        "timestamps": [1.5],
                    },
                )
            )
            contents = Path(result["path"]).read_text(encoding="utf-8")
            assert contents == (
                "# Restarted source\n\nA useful body.\n\n## Source\n\n"
                "- Source: A useful explanation\n"
                "- URL: https://www.youtube.com/watch?v=video-123\n"
                "- Creator: A creator\n"
                "- Relevant timestamps: [00:01](https://www.youtube.com/watch?v=video-123&t=1s)\n"
            )

    asyncio.run(run_server())
    asyncio.run(run_server())


def test_mcp_cache_refresh_filter_variants_and_corruption_errors(tmp_path, monkeypatch):
    from dataclasses import replace

    source = _sample_source()

    def extract(url, exclude_sponsors=True):
        if exclude_sponsors:
            return source
        lookup = ExclusionLookup((), ExclusionStatus.DISABLED, "SponsorBlock", ())
        return replace(
            source,
            exclusion_lookup=lookup,
            filtered_transcript=filter_source_transcript(
                source.metadata, source.transcript, lookup
            ),
        )

    extraction = MagicMock(side_effect=extract)
    monkeypatch.setattr(source_cache, "ingest_source", extraction)
    vault = tmp_path / "vault"
    vault.mkdir()
    database = tmp_path / "cache.db"
    server = mcp_server.create_server(DraftWriterSettings(vault), cache_db=database)

    async def exercise() -> None:
        async with Client(server) as client:
            first = _tool_data(await client.call_tool("ingest_source", {"url": URL}))
            equivalent = _tool_data(
                await client.call_tool("ingest_source", {"url": "https://youtu.be/video-123"})
            )
            assert equivalent["cached"] is True
            assert equivalent["source_id"] == first["source_id"]

            unfiltered = _tool_data(
                await client.call_tool("ingest_source", {"url": URL, "exclude_sponsors": False})
            )
            assert unfiltered["source_id"] != first["source_id"]
            assert unfiltered["cached"] is False
            refreshed = _tool_data(
                await client.call_tool(
                    "ingest_source", {"url": "https://youtu.be/video-123", "refresh": True}
                )
            )
            assert refreshed["source_id"] == first["source_id"]
            assert refreshed["cached"] is False
            unaffected = _tool_data(
                await client.call_tool("ingest_source", {"url": URL, "exclude_sponsors": False})
            )
            assert unaffected["cached"] is True
            assert unaffected["source_id"] == unfiltered["source_id"]

            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "UPDATE sources SET payload = 'corrupt' WHERE source_id = ?",
                    (first["source_id"],),
                )
            broken = await client.call_tool("ingest_source", {"url": URL})
            assert broken.is_error
            assert "Invalid source cache entry" in broken.content[0].text
            repaired = _tool_data(
                await client.call_tool("ingest_source", {"url": URL, "refresh": True})
            )
            assert repaired["source_id"] == first["source_id"]
            assert repaired["cached"] is False

    asyncio.run(exercise())
    assert extraction.call_count == 4


def test_scoped_vault_tools_are_only_exposed_with_trusted_settings(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Engineering").mkdir(parents=True)
    (vault / "Private").mkdir()
    (vault / "00 Inbox/AI Drafts").mkdir(parents=True)
    (vault / "Engineering/Retry.md").write_text("Retry budgets limit load.", encoding="utf-8")
    (vault / "Private/Secret.md").write_text("hidden marker", encoding="utf-8")
    settings = DraftWriterSettings(vault)
    legacy = mcp_server.create_server(settings, cache_db=tmp_path / "legacy.db")
    configured = mcp_server.create_server(
        settings,
        cache_db=tmp_path / "configured.db",
        vault_settings=VaultSettings(vault, ("*",), ("Private",)),
    )

    async def exercise() -> None:
        async with Client(legacy) as client:
            assert "search_vault" not in {tool.name for tool in (await client.list_tools()).tools}
        async with Client(configured) as client:
            assert {"search_vault", "read_vault_note"} <= {
                tool.name for tool in (await client.list_tools()).tools
            }
            assert "create_edit_suggestion" in {
                tool.name for tool in (await client.list_tools()).tools
            }
            result = await client.call_tool("search_vault", {"query": "retry"})
            assert result.structured_content is None
            assert (
                len(
                    json.dumps(
                        {"content": [{"type": "text", "text": result.content[0].text}]},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                )
                <= 16 * 1024
            )
            page = json.loads(result.content[0].text)
            assert page["notes"][0]["path"] == "Engineering/Retry.md"
            assert "hidden marker" not in result.content[0].text
            note_id = page["notes"][0]["note_id"]
            note = json.loads(
                (await client.call_tool("read_vault_note", {"note_id": note_id, "limit": 10}))
                .content[0]
                .text
            )
            assert note["text"] == "Retry budg"
            excluded = await client.call_tool("read_vault_note", {"note_id": "note_unknown"})
            assert excluded.is_error
            assert "NOTE_UNAVAILABLE" in excluded.content[0].text
            assert "Private" not in excluded.content[0].text

    asyncio.run(exercise())


def test_related_links_and_edit_suggestions_are_verified_create_only_drafts(tmp_path, monkeypatch):
    source = _sample_source()
    monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: source)
    vault = tmp_path / "vault"
    (vault / "Engineering").mkdir(parents=True)
    (vault / "Computer Science").mkdir()
    (vault / "Papers").mkdir()
    (vault / "00 Inbox/AI Drafts").mkdir(parents=True)
    target = vault / "Engineering/Retry Policies.md"
    original = "Existing canonical note, unchanged.\n"
    target.write_text(original, encoding="utf-8")
    (vault / "Computer Science/Retry Policies.md").write_text(
        "Different duplicate basename.", encoding="utf-8"
    )
    (vault / "Papers/Café (retry), policy.md").write_text(
        "Unicode marker for punctuation link testing.", encoding="utf-8"
    )
    settings = DraftWriterSettings(vault)
    server = mcp_server.create_server(
        settings,
        cache_db=tmp_path / "cache.db",
        vault_settings=VaultSettings(vault, ("*",), ("Private",)),
    )

    async def exercise() -> None:
        async with Client(server) as client:
            source_id = _tool_data(await client.call_tool("ingest_source", {"url": URL}))[
                "source_id"
            ]
            search = json.loads(
                (await client.call_tool("search_vault", {"query": "retry"})).content[0].text
            )
            selected = next(
                note for note in search["notes"] if note["path"] == "Engineering/Retry Policies.md"
            )
            note_ref = {"note_id": selected["note_id"]}
            modified = "Existing canonical note with a harmless human edit.\n"
            target.write_text(modified, encoding="utf-8")

            related = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {
                        "source_id": source_id,
                        "contents": "# Retry budgets\n\nA retry budget limits aggregate load.",
                        "related_notes": [
                            note_ref
                            | {
                                "reason": "Explains the per-request policy.",
                                "revision": selected["revision"],
                            }
                        ],
                        "timestamps": [125.9],
                    },
                )
            )
            related_text = Path(related["path"]).read_text(encoding="utf-8")
            assert (
                "## Related Notes\n\n- [[Engineering/Retry Policies.md]]: Explains the per-request policy."
                in related_text
            )
            assert "[02:05](https://www.youtube.com/watch?v=video-123&t=125s)" in related_text

            unicode_search = json.loads(
                (await client.call_tool("search_vault", {"query": "unicode marker"}))
                .content[0]
                .text
            )
            unicode_note = unicode_search["notes"][0]
            unicode_draft = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {
                        "source_id": source_id,
                        "contents": "# Unicode path\n\nThis is the related insight.",
                        "related_notes": [
                            {
                                "note_id": unicode_note["note_id"],
                                "reason": "The note contains the specific example.",
                            }
                        ],
                    },
                )
            )
            unicode_text = Path(unicode_draft["path"]).read_text(encoding="utf-8")
            assert "[[Papers/Café (retry), policy.md]]" in unicode_text

            search = json.loads(
                (await client.call_tool("search_vault", {"query": "retry"})).content[0].text
            )
            selected = next(
                note for note in search["notes"] if note["path"] == "Engineering/Retry Policies.md"
            )

            edit = _tool_data(
                await client.call_tool(
                    "create_edit_suggestion",
                    {
                        "source_id": source_id,
                        "target_note_id": selected["note_id"],
                        "target_revision": selected["revision"],
                        "change_summary": "Add an aggregate retry budget.",
                        "reasoning": "The current explanation omits system-wide load.",
                        "proposed_content": "A retry budget caps retries across clients.",
                        "timestamps": [125],
                    },
                )
            )
            edit_path = Path(edit["path"])
            edit_text = edit_path.read_text(encoding="utf-8")
            assert edit_path.name == "edit-retry-policies.md"
            assert edit_text.startswith("[[Engineering/Retry Policies.md]]\n\n## Suggested Change")
            assert "## Proposed Content\n\nA retry budget caps retries across clients." in edit_text
            assert "## Source" in edit_text
            assert target.read_text(encoding="utf-8") == modified
            collision = _tool_data(
                await client.call_tool(
                    "create_edit_suggestion",
                    {
                        "source_id": source_id,
                        "target_note_id": selected["note_id"],
                        "target_revision": selected["revision"],
                        "change_summary": "Add an aggregate retry budget.",
                        "reasoning": "The current explanation omits system-wide load.",
                        "proposed_content": "A retry budget caps retries across clients.",
                    },
                )
            )
            assert Path(collision["path"]).name == "edit-retry-policies-2.md"
            assert target.read_text(encoding="utf-8") == modified

            invalid = await client.call_tool(
                "create_draft",
                {
                    "source_id": source_id,
                    "contents": "# Broken link\n\nBody",
                    "related_notes": [
                        {
                            "note_id": "note_unknown",
                            "revision": "sha256:fake",
                            "reason": "Not verified.",
                        }
                    ],
                },
            )
            assert invalid.is_error
            assert "NOTE_UNAVAILABLE" in invalid.content[0].text
            assert not (vault / "00 Inbox/AI Drafts/broken-link.md").exists()

            injected = await client.call_tool(
                "create_draft",
                {
                    "source_id": source_id,
                    "contents": "# User wikilink\n\nBody with [[Unverified Note]] and [a Markdown link](https://example.com).",
                },
            )
            assert not injected.is_error
            injected_path = Path(_tool_data(injected)["path"])
            injected_text = injected_path.read_text(encoding="utf-8")
            assert "[[Unverified Note]]" in injected_text
            assert "[a Markdown link](https://example.com)" in injected_text

    asyncio.run(exercise())


def _cli_json(monkeypatch, capsys, *args: str):
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", *args])
    cli.main()
    return json.loads(capsys.readouterr().out)


def _cli_error(monkeypatch, capsys, *args: str) -> dict:
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", *args])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    return json.loads(captured.err)["error"]


def _mcp_error_text(result) -> str:
    """Strip the MCP framework's envelope prefix to get the shared `CODE: message` text."""
    assert result.is_error
    text = result.content[0].text
    return text.split(": ", 1)[1] if text.startswith("Error executing tool ") else text


def _without_reuse(ingest_result: dict) -> dict:
    copy = json.loads(json.dumps(ingest_result))
    copy["overview"]["diagnostics"]["acquisition"].pop("reused_from_cache")
    return copy


@pytest.fixture
def vtt_only_youtube(monkeypatch):
    """Fake yt-dlp offering only WebVTT captions that end 290 s before the video does."""
    from obsidian_ingest.extractor.providers import youtube
    from obsidian_ingest.extractor.providers.youtube import sponsorblock

    factory = MagicMock()
    ydl = factory.return_value.__enter__.return_value
    ydl.extract_info.return_value = {
        "id": "video-123",
        "title": "VTT only",
        "duration": 300,
        "subtitles": {"en": [{"ext": "vtt", "url": "https://example.com/vtt"}]},
    }
    ydl.urlopen.return_value.read.return_value = (
        b"WEBVTT\n\n00:00.000 --> 00:05.000\nSpoken start\n\n"
        b"00:04.000 --> 00:10.000\nOverlapping end\n"
    )
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(
        sponsorblock, "fetch_exclusions", MagicMock(side_effect=RuntimeError("SponsorBlock down"))
    )
    return ydl


def test_cli_and_mcp_expose_identical_vtt_diagnostics(
    tmp_path, monkeypatch, capsys, vtt_only_youtube
):
    database = tmp_path / "cache.db"
    vault = tmp_path / "vault"
    vault.mkdir()
    cli_ingest = _cli_json(monkeypatch, capsys, "ingest-source", URL, "--cache-db", str(database))
    diagnostics = cli_ingest["overview"]["diagnostics"]
    payload = vtt_only_youtube.urlopen.return_value.read.return_value
    assert diagnostics["caption"] == {
        "language": "en",
        "origin": "manual",
        "format": "vtt",
        "translated": None,
        "original_payload_bytes": len(payload),
        "original_payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    assert diagnostics["acquisition"]["extractor"] == "yt-dlp"
    assert diagnostics["acquisition"]["acquired_at"].endswith("+00:00")
    assert diagnostics["acquisition"]["reused_from_cache"] is False
    sponsor_filtering = cli_ingest["overview"]["sponsor_filtering"]
    assert (sponsor_filtering["status"], sponsor_filtering["error"]) == (
        "failed",
        "SponsorBlock down",
    )
    assert diagnostics["timing"]["last_caption_end"] == 10.0
    assert diagnostics["timing"]["provider_duration_seconds"] == 300.0
    assert diagnostics["timing"]["gaps"] == [{"start": 10.0, "end": 300.0, "position": "trailing"}]
    source_id = cli_ingest["source_id"]
    cli_overview = _cli_json(
        monkeypatch, capsys, "get-source-overview", source_id, "--cache-db", str(database)
    )
    assert cli_overview["diagnostics"]["acquisition"]["reused_from_cache"] is None
    vtt_only_youtube.extract_info.side_effect = AssertionError("reuse must not re-acquire")
    server = mcp_server.create_server(DraftWriterSettings(vault), cache_db=database)

    async def exercise() -> None:
        async with Client(server) as client:
            ingested = _tool_data(await client.call_tool("ingest_source", {"url": URL}))
            assert ingested["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] is True
            assert _without_reuse(ingested) == _without_reuse(cli_ingest) | {"cached": True}
            overview = _tool_data(
                await client.call_tool("get_source_overview", {"source_id": source_id})
            )
            assert overview == cli_overview

    asyncio.run(exercise())


def test_ingest_and_cache_errors_have_the_same_meaning_in_cli_and_mcp(
    tmp_path, monkeypatch, capsys
):
    from obsidian_ingest.extractor.acquisition import CaptionError

    vault = tmp_path / "vault"
    vault.mkdir()
    database = tmp_path / "cache.db"
    monkeypatch.setattr(
        source_cache,
        "ingest_source",
        MagicMock(side_effect=CaptionError("Caption response is not valid WebVTT")),
    )
    server = mcp_server.create_server(DraftWriterSettings(vault), cache_db=database)
    old = tmp_path / "old-cache.db"
    with closing(sqlite3.connect(old)) as connection, connection:
        connection.execute("CREATE TABLE sources (source_id TEXT)")
        connection.execute("PRAGMA user_version = 1")
    # An incompatible cache must not prevent the MCP server from starting.
    old_server = mcp_server.create_server(DraftWriterSettings(vault), cache_db=old)

    cases = [
        (
            ("ingest-source", URL, "--cache-db", str(database)),
            server,
            "ingest_source",
            {"url": URL},
        ),
        (
            ("ingest-source", "https://example.com/video", "--cache-db", str(database)),
            server,
            "ingest_source",
            {"url": "https://example.com/video"},
        ),
        (
            ("get-source-overview", "abc", "--cache-db", str(old)),
            old_server,
            "get_source_overview",
            {"source_id": "abc"},
        ),
    ]
    cli_errors = [_cli_error(monkeypatch, capsys, *args) for args, *_ in cases]
    assert [error["code"] for error in cli_errors] == [
        "INGEST_FAILED",
        "INVALID_ARGUMENT",
        "SOURCE_CACHE_ERROR",
    ]
    assert "not valid WebVTT" in cli_errors[0]["message"]
    reset = cli_errors[2]["message"]
    assert f"Incompatible source cache schema version 1 at {old}" in reset
    assert f"delete {old}" in reset and "ingest URLs again" in reset

    async def exercise() -> list[str]:
        texts = []
        for _, instance, tool, arguments in cases:
            async with Client(instance) as client:
                texts.append(_mcp_error_text(await client.call_tool(tool, arguments)))
        return texts

    assert asyncio.run(exercise()) == [
        f"{error['code']}: {error['message']}" for error in cli_errors
    ]
    with closing(sqlite3.connect(old)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_cli_and_mcp_return_identical_results_except_identifiers(tmp_path, monkeypatch, capsys):
    source = _sample_source()
    monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: source)
    vault = tmp_path / "vault"
    (vault / "Engineering").mkdir(parents=True)
    (vault / "Private").mkdir()
    (vault / "00 Inbox/AI Drafts").mkdir(parents=True)
    for index in range(3):
        (vault / f"Engineering/Retry {index}.md").write_text("Retry budgets.", encoding="utf-8")
    (vault / "Private/Retry secret.md").write_text("Retry secret.", encoding="utf-8")
    config = tmp_path / "settings.toml"
    config.write_text(
        f'[vault]\nroot = "{vault}"\nallowed = ["*"]\nexcluded = ["Private"]\n', encoding="utf-8"
    )
    database = tmp_path / "cache.db"
    vault_settings = VaultSettings(vault.resolve(), ("*",), ("Private",))
    server = mcp_server.create_server(
        DraftWriterSettings(vault.resolve()), cache_db=database, vault_settings=vault_settings
    )
    cache_args = ("--cache-db", str(database))
    cli_ingest = _cli_json(monkeypatch, capsys, "ingest-source", URL, *cache_args)
    source_id = cli_ingest["source_id"]
    cli_results = {
        "overview": _cli_json(monkeypatch, capsys, "get-source-overview", source_id, *cache_args),
        "chapters": _cli_json(monkeypatch, capsys, "list-chapters", source_id, *cache_args),
        "exclusions": _cli_json(monkeypatch, capsys, "list-exclusions", source_id, *cache_args),
        "transcript": _cli_json(
            monkeypatch, capsys, "read-transcript", source_id, "--limit", "2", *cache_args
        ),
        "chapter": _cli_json(
            monkeypatch, capsys, "read-chapter", source_id, "0", "--offset", "1", *cache_args
        ),
    }
    cli_search = _cli_json(
        monkeypatch, capsys, "search-vault", "retry", "--limit", "2", "--config", str(config)
    )

    async def exercise() -> None:
        async with Client(server) as client:
            ingested = _tool_data(await client.call_tool("ingest_source", {"url": URL}))
            # Only cache reuse differs: the CLI acquired, MCP then reused that capture.
            assert (
                cli_ingest["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] is False
            )
            assert ingested["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] is True
            assert _without_reuse(ingested) == _without_reuse(cli_ingest) | {"cached": True}
            # This fake source's captions end at 3 s of a 600 s video.
            assert ingested["overview"]["diagnostics"]["timing"]["gaps"] == [
                {"start": 3.0, "end": 600.0, "position": "trailing"}
            ]
            assert (
                cli_results["overview"]["diagnostics"]["acquisition"]["reused_from_cache"] is None
            )
            calls = {
                "overview": ("get_source_overview", {}),
                "chapters": ("list_chapters", {}),
                "exclusions": ("list_exclusions", {}),
                "transcript": ("read_transcript", {"limit": 2}),
                "chapter": ("read_chapter", {"chapter_index": 0, "offset": 1}),
            }
            for key, (tool, arguments) in calls.items():
                result = await client.call_tool(tool, {"source_id": source_id} | arguments)
                assert _tool_data(result) == cli_results[key], key
            search = json.loads(
                (await client.call_tool("search_vault", {"query": "retry", "limit": 2}))
                .content[0]
                .text
            )
            assert all(note.pop("note_id").startswith("note_") for note in search["notes"])
            assert search == cli_search
            # The search cursor is shared: an MCP cursor continues a CLI search and vice versa.
            continued = json.loads(
                (
                    await client.call_tool(
                        "search_vault",
                        {"query": "retry", "limit": 2, "cursor": cli_search["next_cursor"]},
                    )
                )
                .content[0]
                .text
            )
            assert [note["path"] for note in continued["notes"]] == ["Engineering/Retry 2.md"]
            errors = {
                "SOURCE_NOT_FOUND": ("read_transcript", {"source_id": "missing"}),
                "INVALID_ARGUMENT": ("read_transcript", {"source_id": source_id, "offset": -1}),
                "INVALID_CURSOR": ("search_vault", {"query": "retry", "cursor": "bogus"}),
                "NOTE_UNAVAILABLE": ("read_vault_note", {"note_id": "note_unknown"}),
            }
            for code, (tool, arguments) in errors.items():
                failure = await client.call_tool(tool, arguments)
                assert failure.is_error
                assert f"{code}: " in failure.content[0].text

    asyncio.run(exercise())


def test_mcp_search_cursor_survives_restart_but_not_scope_change(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Notes").mkdir(parents=True)
    (vault / "Private").mkdir()
    (vault / "00 Inbox/AI Drafts").mkdir(parents=True)
    for index in range(3):
        (vault / f"Notes/Fixture {index}.md").write_text("fixture", encoding="utf-8")
    settings = DraftWriterSettings(vault)

    def server(excluded: tuple[str, ...]):
        return mcp_server.create_server(
            settings,
            cache_db=tmp_path / "cache.db",
            vault_settings=VaultSettings(vault, ("*",), excluded),
        )

    async def search(instance, arguments: dict):
        async with Client(instance) as client:
            return await client.call_tool("search_vault", arguments)

    first = json.loads(
        asyncio.run(search(server(("Private",)), {"query": "fixture", "limit": 2})).content[0].text
    )
    resumed = asyncio.run(
        search(
            server(("Private",)), {"query": "fixture", "limit": 2, "cursor": first["next_cursor"]}
        )
    )
    assert [note["path"] for note in json.loads(resumed.content[0].text)["notes"]] == [
        "Notes/Fixture 2.md"
    ]
    rejected = asyncio.run(
        search(
            server(("Private", "Notes/Fixture 2.md")),
            {"query": "fixture", "limit": 2, "cursor": first["next_cursor"]},
        )
    )
    assert rejected.is_error
    assert "INVALID_CURSOR: " in rejected.content[0].text


def test_mcp_rejects_malformed_argument_types_as_invalid_argument(tmp_path, monkeypatch):
    """Lenient SDK coercion must not accept what the shared layer and the CLI reject."""
    source = _sample_source()
    monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: source)
    vault = tmp_path / "vault"
    (vault / "Notes").mkdir(parents=True)
    (vault / "00 Inbox/AI Drafts").mkdir(parents=True)
    (vault / "Notes/Retry.md").write_text("Retry budgets.", encoding="utf-8")
    server = mcp_server.create_server(
        DraftWriterSettings(vault),
        cache_db=tmp_path / "cache.db",
        vault_settings=VaultSettings(vault, ("*",), ()),
    )

    async def exercise() -> None:
        async with Client(server) as client:
            source_id = _tool_data(await client.call_tool("ingest_source", {"url": URL}))[
                "source_id"
            ]
            draft = {"source_id": source_id, "contents": "# T\n\nBody."}
            malformed = [
                ("read_transcript", {"source_id": source_id, "offset": True}, "offset"),
                ("read_transcript", {"source_id": source_id, "limit": False}, "limit"),
                ("read_transcript", {"source_id": source_id, "offset": "1"}, "offset"),
                ("read_transcript", {"source_id": source_id, "offset": "bad"}, "offset"),
                ("read_transcript", {"source_id": source_id, "limit": 1.5}, "limit"),
                ("read_transcript", {"source_id": source_id, "start": True}, "start"),
                ("read_transcript", {"source_id": source_id, "start": "1"}, "start"),
                ("read_transcript", {"source_id": source_id, "include_excluded": 1}, "include"),
                ("read_transcript", {"source_id": source_id, "include_excluded": "true"}, "incl"),
                ("read_transcript", {"source_id": source_id, "limit": 0}, "limit"),
                ("read_transcript", {"source_id": source_id, "offset": -1}, "offset"),
                ("search_vault", {"query": "retry", "limit": 50}, "limit"),
                ("read_transcript", {"source_id": 123}, "source_id"),
                ("read_transcript", {}, "source_id"),
                ("read_transcript", {"source_id": source_id, "unknown": 1}, "unknown"),
                ("read_chapter", {"source_id": source_id, "chapter_index": True}, "chapter"),
                ("read_chapter", {"source_id": source_id, "chapter_index": "0"}, "chapter"),
                ("ingest_source", {"url": URL, "refresh": "false"}, "refresh"),
                ("ingest_source", {"url": URL, "exclude_sponsors": 0}, "exclude_sponsors"),
                ("search_vault", {"query": "retry", "limit": True}, "limit"),
                ("search_vault", {"query": 5}, "query"),
                ("search_vault", {"scope": "everything"}, "scope"),
                ("read_vault_note", {"note_id": "note_x", "offset": "0"}, "offset"),
                ("create_draft", draft | {"timestamps": [True]}, "timestamps"),
                ("create_draft", draft | {"timestamps": "[1]"}, "timestamps"),
                (
                    "create_draft",
                    draft | {"related_notes": [{"note_id": "n", "reason": "r", "x": 1}]},
                    "related_notes",
                ),
                ("create_draft", draft | {"filename": 5}, "filename"),
            ]
            for tool, arguments, field in malformed:
                result = await client.call_tool(tool, arguments)
                assert result.is_error, (tool, arguments)
                text = _mcp_error_text(result)
                assert text.startswith("INVALID_ARGUMENT: "), (tool, arguments, text)
                assert field in text, (tool, arguments, text)
            # Valid JSON numbers still work: integers for float fields included.
            page = _tool_data(
                await client.call_tool(
                    "read_transcript", {"source_id": source_id, "start": 1, "end": 2.5}
                )
            )
            assert page["offset"] == 0
            assert not (vault / "00 Inbox/AI Drafts/t.md").exists()

    asyncio.run(exercise())
