import asyncio
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client import Client
from mcp.client.stdio import stdio_client

from obsidian_ingest import mcp_server, source_cache
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
                "title",
                "contents",
                "timestamps",
                "related_notes",
            }
            related_schema = draft_tool.input_schema["properties"]["related_notes"]
            assert related_schema["anyOf"][0]["items"]["$ref"] == "#/$defs/RelatedNoteInput"
            related_item_schema = draft_tool.input_schema["$defs"]["RelatedNoteInput"]
            assert set(related_item_schema["required"]) == {"note_id", "reason"}
            assert "revision" in related_item_schema["properties"]
            transcript_tool = next(tool for tool in tools.tools if tool.name == "read_transcript")
            assert transcript_tool.input_schema["properties"]["limit"]["default"] == 40

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
            assert [segment["text"] for segment in first_page["segments"]] == [
                "First atom",
                "Second atom at same time",
            ]
            assert first_page["next_offset"] == 2
            second_page = _tool_data(
                await client.call_tool(
                    "read_transcript", {"source_id": source_id, "offset": 2, "limit": 2}
                )
            )
            assert [segment["text"] for segment in second_page["segments"]] == ["Third atom"]
            assert second_page["next_offset"] is None
            raw_page = _tool_data(
                await client.call_tool(
                    "read_transcript",
                    {"source_id": source_id, "include_excluded": True, "offset": 3},
                )
            )
            assert [segment["text"] for segment in raw_page["segments"]] == ["Sponsor atom"]
            assert raw_page["segments"][0]["source_index"] == 3

            chapter_page = _tool_data(
                await client.call_tool(
                    "read_chapter",
                    {"source_id": source_id, "chapter_index": 0, "limit": 2, "offset": 1},
                )
            )
            assert [segment["text"] for segment in chapter_page["segments"]] == [
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
                        "title": "A useful note",
                        "contents": "Agent-authored content.",
                        "timestamps": [1.0],
                    },
                )
            )
            draft_path = Path(draft["path"])
            assert draft_path == vault / "00 Inbox/AI Drafts/A useful note.md"
            draft_contents = draft_path.read_text(encoding="utf-8")
            assert draft_contents.startswith("Agent-authored content.\n\n## Source")
            assert "https://www.youtube.com/watch?v=video-123" in draft_contents
            assert "1s" in draft_contents

            invalid_draft = await client.call_tool(
                "create_draft",
                {
                    "source_id": source_id,
                    "title": "Invalid formatting",
                    "contents": "# Agent title\n\nBody",
                },
            )
            assert invalid_draft.is_error
            assert "must not start with an H1 title" in invalid_draft.content[0].text
            assert not (vault / "00 Inbox/AI Drafts/Invalid formatting.md").exists()

            referenced_draft = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {
                        "source_id": source_id,
                        "title": "References included",
                        "contents": "Body.\n\n## References\n- [Paper](https://example.com)",
                    },
                )
            )
            reference_text = Path(referenced_draft["path"]).read_text(encoding="utf-8")
            assert reference_text.count("## Source") == 1
            assert "## References\n- [Paper](https://example.com)\n\n## Source" in reference_text

            missing = await client.call_tool("read_transcript", {"source_id": "not-ingested"})
            assert missing.is_error
            assert "Unknown source_id" in missing.content[0].text

            invalid_page = await client.call_tool(
                "read_transcript", {"source_id": source_id, "limit": 81}
            )
            assert invalid_page.is_error
            assert "limit must be" in invalid_page.content[0].text

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
                        "title": "After restart",
                        "contents": "Useful content.",
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
        assert [segment["text"] for segment in actual] == [segment.text for segment in segments]
        assert [segment["source_index"] for segment in actual] == list(range(180))
        assert all(page["total_segments"] == 180 for page in pages)
        assert all(page["limit"] == mcp_server.DEFAULT_PAGE_SIZE for page in pages)


def test_single_oversized_atom_fails_without_truncation():
    segment = TranscriptSegment("x" * mcp_server.MAX_PAGE_ENVELOPE_BYTES, 0, 1)
    with pytest.raises(ValueError, match="segment 7 exceeds"):
        mcp_server._segment_page(((7, segment),), 0, 40)


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
            assert page["segments"][0]["text"] == "First atom"
            result = _tool_data(
                await client.call_tool(
                    "create_draft",
                    {
                        "source_id": seeded.source_id,
                        "title": "Restarted source",
                        "contents": "A useful body.",
                        "timestamps": [1.5],
                    },
                )
            )
            contents = Path(result["path"]).read_text(encoding="utf-8")
            assert contents == (
                "A useful body.\n\n## Source\n\n"
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
                        "title": "Retry budgets",
                        "contents": "A retry budget limits aggregate load.",
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
                        "title": "Unicode path",
                        "contents": "This is the related insight.",
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
            assert edit_path.name.startswith("(EDIT) Retry Policies")
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
            assert Path(collision["path"]).name == "(EDIT) Retry Policies (2).md"
            assert target.read_text(encoding="utf-8") == modified

            invalid = await client.call_tool(
                "create_draft",
                {
                    "source_id": source_id,
                    "title": "Broken link",
                    "contents": "Body",
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
            assert not (vault / "00 Inbox/AI Drafts/Broken link.md").exists()

            injected = await client.call_tool(
                "create_draft",
                {
                    "source_id": source_id,
                    "title": "User wikilink",
                    "contents": "Body with [[Unverified Note]] and [a Markdown link](https://example.com).",
                },
            )
            assert not injected.is_error
            injected_path = Path(_tool_data(injected)["path"])
            injected_text = injected_path.read_text(encoding="utf-8")
            assert "[[Unverified Note]]" in injected_text
            assert "[a Markdown link](https://example.com)" in injected_text

    asyncio.run(exercise())
