import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from mcp.client import Client

from obsidian_ingest import mcp_server
from obsidian_ingest.drafts import DraftWriterSettings
from obsidian_ingest.extractor import Provider
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
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
        ),
    )
    return Source(metadata, transcript)


def _tool_data(result) -> dict:
    assert not result.is_error
    data = result.structured_content
    return data.get("result", data) if len(data) == 1 and "result" in data else data


def test_stdio_server_exposes_source_navigation_and_bound_draft_writer(
    monkeypatch, tmp_path: Path
) -> None:
    source = _sample_source()
    ingest = MagicMock(return_value=source)
    monkeypatch.setattr(mcp_server, "ingest_application_source", ingest)
    vault = tmp_path / "vault"
    vault.mkdir()
    server = mcp_server.create_server(DraftWriterSettings(vault))

    async def exercise_tools() -> None:
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert names == {
                "ingest_source",
                "get_source_overview",
                "list_chapters",
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
            }

            ingested = _tool_data(await client.call_tool("ingest_source", {"url": URL}))
            source_id = ingested["source_id"]
            assert ingested["overview"]["title"] == "A useful explanation"
            assert ingested["overview"]["provider"] == Provider.YOUTUBE.value
            assert ingested["overview"]["chapter_count"] == 1

            overview = _tool_data(
                await client.call_tool("get_source_overview", {"source_id": source_id})
            )
            assert overview["description"] == "Context for the topic"

            chapters = _tool_data(await client.call_tool("list_chapters", {"source_id": source_id}))
            assert chapters == [{"index": 0, "title": "Main idea", "start": 0.0, "end": 20.0}]

            first_page = _tool_data(
                await client.call_tool(
                    "read_transcript", {"source_id": source_id, "offset": 0, "limit": 2}
                )
            )
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

            missing = await client.call_tool("read_transcript", {"source_id": "not-ingested"})
            assert missing.is_error
            assert "Unknown source_id" in missing.content[0].text

            invalid_page = await client.call_tool(
                "read_transcript", {"source_id": source_id, "limit": 501}
            )
            assert invalid_page.is_error
            assert "limit must be" in invalid_page.content[0].text

    asyncio.run(exercise_tools())
    ingest.assert_called_once_with(URL)


def test_source_handles_are_scoped_to_one_server_process(tmp_path: Path, monkeypatch) -> None:
    source = _sample_source()
    monkeypatch.setattr(mcp_server, "ingest_application_source", lambda url: source)
    vault = tmp_path / "vault"
    vault.mkdir()
    server = mcp_server.create_server(DraftWriterSettings(vault))

    async def ingest_and_read() -> str:
        async with Client(server) as client:
            result = _tool_data(await client.call_tool("ingest_source", {"url": URL}))
            return result["source_id"]

    source_id = asyncio.run(ingest_and_read())
    second_server = mcp_server.create_server(DraftWriterSettings(vault))

    async def read_from_new_server():
        async with Client(second_server) as client:
            return await client.call_tool("get_source_overview", {"source_id": source_id})

    result = asyncio.run(read_from_new_server())
    assert result.is_error
    assert "Unknown source_id" in result.content[0].text
