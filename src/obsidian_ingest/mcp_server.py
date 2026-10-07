"""Thin local MCP adapter for source navigation and create-only draft writing."""

from typing import Any
from uuid import uuid4

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from obsidian_ingest.drafts import DraftWriterSettings
from obsidian_ingest.drafts import create_draft as write_draft
from obsidian_ingest.extractor.metadata import Chapter
from obsidian_ingest.extractor.transcript import TranscriptSegment
from obsidian_ingest.source import (
    Source,
    get_source_overview,
    read_chapter_entries,
    read_transcript_entries,
)
from obsidian_ingest.source import ingest_source as ingest_application_source
from obsidian_ingest.source import list_chapters as get_application_chapters

DEFAULT_PAGE_SIZE = 200
MAX_PAGE_SIZE = 500


def _source_overview(source: Source) -> dict[str, Any]:
    overview = get_source_overview(source)
    metadata = overview.metadata
    return {
        "title": metadata.title,
        "creator": metadata.creator_name,
        "source_url": metadata.original_url,
        "canonical_url": metadata.canonical_url,
        "duration_seconds": metadata.duration_seconds,
        "description": metadata.description,
        "provider": metadata.provider.value,
        "native_format": metadata.native_format.value,
        "content_category": (
            overview.content_category.value if overview.content_category is not None else None
        ),
        "chapter_count": overview.chapter_count,
        "sponsor_filtering": {
            "status": overview.exclusion_lookup.status.value,
            "enabled": bool(overview.exclusion_lookup.categories),
            "categories": list(overview.exclusion_lookup.categories),
            "error": overview.exclusion_lookup.error_message,
            "original_segments": overview.original_segment_count,
            "retained_segments": overview.retained_segment_count,
            "excluded_segments": overview.excluded_segment_count,
        },
    }


def _segment_page(
    entries: tuple[tuple[int, TranscriptSegment], ...], offset: int, limit: int
) -> dict[str, Any]:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be an integer between 1 and {MAX_PAGE_SIZE}")
    selected = entries[offset : offset + limit]
    next_offset = offset + len(selected)
    return {
        "segments": [
            {
                "source_index": source_index,
                "text": segment.text,
                "start": segment.start,
                "duration": segment.duration,
            }
            for source_index, segment in selected
        ],
        "total_segments": len(entries),
        "offset": offset,
        "next_offset": next_offset if next_offset < len(entries) else None,
    }


def _chapter_data(index: int, chapter: Chapter) -> dict[str, Any]:
    return {"index": index, "title": chapter.title, "start": chapter.start, "end": chapter.end}


def create_server(settings: DraftWriterSettings) -> MCPServer:
    """Create an MCP server whose source handles live only for this process."""
    server = MCPServer("obsidian-ingest")
    sources: dict[str, Source] = {}

    def get_source(source_id: str) -> Source:
        try:
            return sources[source_id]
        except KeyError as error:
            raise ToolError(f"Unknown source_id: {source_id}; ingest the URL first") from error

    @server.tool()
    def ingest_source(url: str, exclude_sponsors: bool = True) -> dict[str, Any]:
        """Ingest a source, excluding SponsorBlock sponsor atoms by default."""
        try:
            source = ingest_application_source(url, exclude_sponsors=exclude_sponsors)
        except Exception as error:
            raise ToolError(f"Could not ingest source: {error}") from error
        source_id = uuid4().hex
        sources[source_id] = source
        return {"source_id": source_id, "overview": _source_overview(source)}

    @server.tool()
    def get_source_overview(source_id: str) -> dict[str, Any]:
        """Get source metadata and sponsor-filtering outcome without transcript text."""
        return _source_overview(get_source(source_id))

    @server.tool()
    def list_chapters(source_id: str) -> list[dict[str, Any]]:
        """List creator chapters with zero-based indices."""
        return [
            _chapter_data(index, chapter)
            for index, chapter in enumerate(get_application_chapters(get_source(source_id)))
        ]

    @server.tool()
    def list_exclusions(source_id: str) -> list[dict[str, Any]]:
        """List sponsor ranges and original transcript indices they exclude."""
        source = get_source(source_id)
        return [
            {
                "range_index": range_index,
                "start": exclusion.start,
                "end": exclusion.end,
                "reason": exclusion.reason,
                "provenance": exclusion.provenance,
                "source_indices": [
                    segment.source_index
                    for segment in source.filtered_transcript.excluded
                    if range_index in segment.range_indices
                ],
            }
            for range_index, exclusion in enumerate(source.exclusion_lookup.ranges)
        ]

    @server.tool()
    def read_transcript(
        source_id: str,
        start: float | None = None,
        end: float | None = None,
        include_excluded: bool = False,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Read sponsor-filtered transcript pages by default; optionally include original atoms."""
        source = get_source(source_id)
        try:
            entries = read_transcript_entries(
                source,
                start=start,
                end=end,
                include_excluded=include_excluded,
            )
            return _segment_page(entries, offset, limit)
        except (TypeError, ValueError) as error:
            raise ToolError(str(error)) from error

    @server.tool()
    def read_chapter(
        source_id: str,
        chapter_index: int,
        include_excluded: bool = False,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Read creator-chapter pages from the retained or original transcript view."""
        source = get_source(source_id)
        try:
            entries = read_chapter_entries(
                source,
                chapter_index,
                include_excluded=include_excluded,
            )
            return _segment_page(entries, offset, limit)
        except (IndexError, TypeError, ValueError) as error:
            raise ToolError(str(error)) from error

    @server.tool()
    def create_draft(
        source_id: str,
        title: str,
        contents: str,
        timestamps: list[float] | None = None,
    ) -> dict[str, str]:
        """Create a draft attributed to an ingested source in the configured folder."""
        source = get_source(source_id)
        try:
            result = write_draft(
                settings,
                title=title,
                contents=contents,
                source=source.metadata,
                timestamps=tuple(timestamps or ()),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise ToolError(f"Could not create draft: {error}") from error
        return {"path": str(result.path)}

    return server
