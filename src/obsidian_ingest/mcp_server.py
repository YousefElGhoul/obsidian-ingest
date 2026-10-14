"""Thin local MCP adapter for source navigation and create-only draft writing."""

import json
from pathlib import Path
from typing import Any

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
from obsidian_ingest.source import list_chapters as get_application_chapters
from obsidian_ingest.source_cache import SourceCache, SourceCacheError

DEFAULT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 80
MAX_PAGE_ENVELOPE_BYTES = 20 * 1024


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
) -> str:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be an integer between 1 and {MAX_PAGE_SIZE}")
    segments = []
    for source_index, segment in entries[offset : offset + limit]:
        segments.append(
            {
                "source_index": source_index,
                "text": segment.text,
                "start": segment.start,
                "duration": segment.duration,
            }
        )
        next_offset = offset + len(segments)
        page = {
            "segments": segments,
            "offset": offset,
            "limit": limit,
            "total_segments": len(entries),
            "next_offset": next_offset if next_offset < len(entries) else None,
        }
        text = json.dumps(page, ensure_ascii=False, separators=(",", ":"))
        envelope = json.dumps(
            {"content": [{"type": "text", "text": text}]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        # Leave room for the surrounding JSON-RPC result and server metadata.
        if len(envelope) > MAX_PAGE_ENVELOPE_BYTES - 1024:
            segments.pop()
            if not segments:
                raise ValueError(
                    f"Transcript segment {source_index} exceeds the {MAX_PAGE_ENVELOPE_BYTES}-byte response limit"
                )
            break
    next_offset = offset + len(segments)
    page = {
        "segments": segments,
        "offset": offset,
        "limit": limit,
        "total_segments": len(entries),
        "next_offset": next_offset if next_offset < len(entries) else None,
    }
    return json.dumps(page, ensure_ascii=False, separators=(",", ":"))


def _chapter_data(index: int, chapter: Chapter) -> dict[str, Any]:
    return {"index": index, "title": chapter.title, "start": chapter.start, "end": chapter.end}


def create_server(settings: DraftWriterSettings, *, cache_db: Path | None = None) -> MCPServer:
    """Create an MCP server backed by persistent, option-specific source handles."""
    server = MCPServer("obsidian-ingest")
    cache = SourceCache(cache_db)

    def get_source(source_id: str) -> Source:
        try:
            return cache.get(source_id)
        except KeyError as error:
            raise ToolError(f"Unknown source_id: {source_id}; ingest the URL first") from error
        except SourceCacheError as error:
            raise ToolError(str(error)) from error

    @server.tool()
    def ingest_source(
        url: str, exclude_sponsors: bool = True, refresh: bool = False
    ) -> dict[str, Any]:
        """Reuse a persistent source; refresh explicitly re-extracts. Sponsors excluded by default."""
        try:
            result = cache.ingest(url, exclude_sponsors=exclude_sponsors, refresh=refresh)
        except Exception as error:
            raise ToolError(f"Could not ingest source: {error}") from error
        return {
            "source_id": result.source_id,
            "cached": result.cached,
            "overview": _source_overview(result.source),
        }

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

    @server.tool(structured_output=False)
    def read_transcript(
        source_id: str,
        start: float | None = None,
        end: float | None = None,
        include_excluded: bool = False,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> str:
        """Read compact JSON pages; follow next_offset until null. Whole atoms are preserved."""
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

    @server.tool(structured_output=False)
    def read_chapter(
        source_id: str,
        chapter_index: int,
        include_excluded: bool = False,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> str:
        """Read compact JSON chapter pages; follow next_offset until null."""
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
        """Create a source-attributed draft; contents is the Markdown body only, with no title H1 or source section."""
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
