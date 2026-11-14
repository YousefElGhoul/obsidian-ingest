"""Transport-neutral operations, result construction, limits, and error codes.

The CLI and MCP adapters call these functions so both transports share validation,
limits, result data, error meanings, and side effects. Adapters only translate
identifiers (MCP note handles versus CLI vault-relative paths) and wrap the results
in their own envelopes.
"""

import json
import re
from typing import Any, Literal

from obsidian_ingest.curation import create_edit_suggestion, verified_related_notes
from obsidian_ingest.diagnostics import source_diagnostics
from obsidian_ingest.drafts import DraftResult, DraftWriterSettings, create_draft
from obsidian_ingest.extractor import Provider, detect_provider
from obsidian_ingest.extractor.providers.youtube import extract_video_id
from obsidian_ingest.extractor.transcript import TranscriptSegment
from obsidian_ingest.source import (
    Source,
    get_source_overview,
    list_chapters,
    read_chapter_entries,
    read_transcript_entries,
)
from obsidian_ingest.source_cache import SourceCache, SourceCacheError
from obsidian_ingest.vault import NoteChanged, NoteUnavailable, VaultReader

DEFAULT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 80
MAX_PAGE_ENVELOPE_BYTES = 20 * 1024
# Leave room for the surrounding JSON-RPC result and server metadata.
ENVELOPE_HEADROOM_BYTES = 1024
DEFAULT_SEARCH_LIMIT = 10
DEFAULT_NOTE_READ_LIMIT = 4000

NoteIdentifier = Literal["note_id", "path"]

_CODE_PREFIX = re.compile(r"^([A-Z][A-Z_]+): (.*)$", re.DOTALL)


class OperationError(Exception):
    """An expected failure with a stable code shared by every transport."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def as_operation_error(error: BaseException) -> OperationError | None:
    """Map an expected application exception to a coded error, or None if unexpected."""
    if isinstance(error, OperationError):
        return error
    if isinstance(error, NoteChanged):
        return OperationError("NOTE_CHANGED", "note changed; search and read it again")
    if isinstance(error, NoteUnavailable):
        return OperationError("NOTE_UNAVAILABLE", "note is unavailable")
    if isinstance(error, SourceCacheError):
        return OperationError("SOURCE_CACHE_ERROR", str(error))
    if isinstance(error, (ValueError, TypeError, IndexError)):
        text = str(error)
        match = _CODE_PREFIX.match(text)
        if match:
            return OperationError(match.group(1), match.group(2))
        return OperationError("INVALID_ARGUMENT", text)
    if isinstance(error, (OSError, RuntimeError)):
        return OperationError("WRITE_FAILED", str(error))
    return None


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def fits_envelope(value: Any, max_bytes: int) -> bool:
    """Return whether value fits one MCP text-content envelope with protocol headroom."""
    envelope = compact_json({"content": [{"type": "text", "text": compact_json(value)}]})
    return len(envelope.encode("utf-8")) <= max_bytes - ENVELOPE_HEADROOM_BYTES


def source_overview(source: Source, *, cached: bool | None = None) -> dict[str, Any]:
    """Build the one source overview result shared by ingest and overview operations.

    ``cached`` (cache reuse) is known only at ingest; reads by handle report None.
    """
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
        "diagnostics": source_diagnostics(source, cached=cached),
    }


def _validate_ingest_request(url: object, exclude_sponsors: object, refresh: object) -> None:
    """Reject malformed requests before acquisition, so later failures mean INGEST_FAILED."""
    if not isinstance(url, str) or not url.strip():
        raise OperationError("INVALID_ARGUMENT", "url must be a nonempty string")
    if not isinstance(exclude_sponsors, bool) or not isinstance(refresh, bool):
        raise OperationError("INVALID_ARGUMENT", "exclude_sponsors and refresh must be booleans")
    try:
        provider = detect_provider(url)
        if provider != Provider.YOUTUBE:
            raise ValueError(f"Source ingestion is not supported for provider: {provider}")
        extract_video_id(url)
    except ValueError as error:
        raise OperationError("INVALID_ARGUMENT", str(error)) from error


def ingest(
    cache: SourceCache, url: str, *, exclude_sponsors: bool = True, refresh: bool = False
) -> dict[str, Any]:
    _validate_ingest_request(url, exclude_sponsors, refresh)
    try:
        result = cache.ingest(url, exclude_sponsors=exclude_sponsors, refresh=refresh)
    except SourceCacheError:
        # Mapped verbatim to SOURCE_CACHE_ERROR; messages carry reset instructions.
        raise
    except Exception as error:
        # Includes CaptionError and any other acquisition-time failure.
        raise OperationError("INGEST_FAILED", f"Could not ingest source: {error}") from error
    return {
        "source_id": result.source_id,
        "cached": result.cached,
        "overview": source_overview(result.source, cached=result.cached),
    }


def load_source(cache: SourceCache, source_id: str) -> Source:
    if not isinstance(source_id, str) or not source_id:
        raise OperationError("INVALID_ARGUMENT", "source_id must be a nonempty string")
    try:
        return cache.get(source_id)
    except KeyError as error:
        raise OperationError(
            "SOURCE_NOT_FOUND", f"Unknown source_id: {source_id}; ingest the URL first"
        ) from error


def chapters(source: Source) -> list[dict[str, Any]]:
    return [
        {"index": index, "title": chapter.title, "start": chapter.start, "end": chapter.end}
        for index, chapter in enumerate(list_chapters(source))
    ]


def exclusions(source: Source) -> list[dict[str, Any]]:
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


def _segment_page_data(
    segments: list[dict[str, Any]], offset: int, limit: int, total: int
) -> dict[str, Any]:
    next_offset = offset + len(segments)
    return {
        "segments": segments,
        "offset": offset,
        "limit": limit,
        "total_segments": total,
        "next_offset": next_offset if next_offset < total else None,
    }


def segment_page(
    entries: tuple[tuple[int, TranscriptSegment], ...], offset: int, limit: int
) -> dict[str, Any]:
    """Page whole transcript atoms, shrinking the page to fit the response limit."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be an integer between 1 and {MAX_PAGE_SIZE}")
    segments: list[dict[str, Any]] = []
    for source_index, segment in entries[offset : offset + limit]:
        segments.append(
            {
                "source_index": source_index,
                "text": segment.text,
                "start": segment.start,
                "duration": segment.duration,
            }
        )
        page = _segment_page_data(segments, offset, limit, len(entries))
        if not fits_envelope(page, MAX_PAGE_ENVELOPE_BYTES):
            segments.pop()
            if not segments:
                raise ValueError(
                    f"Transcript segment {source_index} exceeds the "
                    f"{MAX_PAGE_ENVELOPE_BYTES}-byte response limit"
                )
            break
    return _segment_page_data(segments, offset, limit, len(entries))


def transcript_page(
    source: Source,
    *,
    start: float | None = None,
    end: float | None = None,
    include_excluded: bool = False,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    entries = read_transcript_entries(
        source, start=start, end=end, include_excluded=include_excluded
    )
    return segment_page(entries, offset, limit)


def chapter_page(
    source: Source,
    chapter_index: int,
    *,
    include_excluded: bool = False,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    entries = read_chapter_entries(source, chapter_index, include_excluded=include_excluded)
    return segment_page(entries, offset, limit)


def _with_identifier(item: dict[str, Any], identifier: NoteIdentifier) -> dict[str, Any]:
    """Keep only identifiers that the calling transport accepts on follow-up calls."""
    if identifier == "path":
        return {key: value for key, value in item.items() if key != "note_id"}
    return item


def search_vault(
    vault: VaultReader,
    query: str = "",
    *,
    scope: str = "both",
    cursor: str | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    identifier: NoteIdentifier = "note_id",
) -> dict[str, Any]:
    result = vault.search(query, scope=scope, cursor=cursor, limit=limit)
    return result | {"notes": [_with_identifier(note, identifier) for note in result["notes"]]}


def read_vault_note(
    vault: VaultReader,
    note_id: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_NOTE_READ_LIMIT,
    revision: str | None = None,
    identifier: NoteIdentifier = "note_id",
) -> dict[str, Any]:
    page = vault.read(note_id, offset=offset, limit=limit, revision=revision)
    return _with_identifier(page, identifier)


def _path_result(result: DraftResult) -> dict[str, str]:
    return {"path": str(result.path)}


def write_draft(
    settings: DraftWriterSettings,
    source: Source,
    *,
    title: str,
    contents: str,
    timestamps: tuple[float, ...] = (),
    related_notes: list[dict[str, str]] | None = None,
    vault: VaultReader | None = None,
) -> dict[str, str]:
    """Create a draft; related notes are `{note_id, reason}` handles from `vault`."""
    verified: tuple[tuple[str, str], ...] = ()
    if related_notes:
        if vault is None:
            raise OperationError(
                "NOTE_UNAVAILABLE", "verified vault links require trusted vault settings"
            )
        verified = verified_related_notes(vault, related_notes)
    result = create_draft(
        settings,
        title=title,
        contents=contents,
        source=source.metadata,
        timestamps=timestamps,
        related_notes=verified,
    )
    return _path_result(result)


def write_edit_suggestion(
    settings: DraftWriterSettings,
    source: Source,
    vault: VaultReader,
    *,
    target_note_id: str,
    target_revision: str,
    change_summary: str,
    reasoning: str,
    proposed_content: str,
    timestamps: tuple[float, ...] = (),
) -> dict[str, str]:
    result = create_edit_suggestion(
        settings,
        source=source.metadata,
        vault=vault,
        target_note_id=target_note_id,
        target_revision=target_revision,
        change_summary=change_summary,
        reasoning=reasoning,
        proposed_content=proposed_content,
        timestamps=timestamps,
    )
    return _path_result(result)
