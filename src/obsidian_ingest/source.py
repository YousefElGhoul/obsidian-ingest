"""Normalized source ingestion and read-only navigation, independent of adapters."""

from dataclasses import dataclass
from math import isfinite

from obsidian_ingest.extractor import ContentCategory, Provider, detect_provider
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExclusionLookup,
    SourceMetadata,
    categorize_content,
)
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import FilteredTranscript, filter_source_transcript


@dataclass(frozen=True, slots=True)
class Source:
    metadata: SourceMetadata
    transcript: Transcript
    exclusion_lookup: ExclusionLookup
    filtered_transcript: FilteredTranscript


@dataclass(frozen=True, slots=True)
class SourceOverview:
    metadata: SourceMetadata
    content_category: ContentCategory | None
    chapter_count: int
    exclusion_lookup: ExclusionLookup
    original_segment_count: int
    retained_segment_count: int
    excluded_segment_count: int


def ingest_source(url: str, *, exclude_sponsors: bool = True) -> Source:
    """Fetch one normalized source; sponsor filtering is best-effort and on by default."""
    if not isinstance(exclude_sponsors, bool):
        raise TypeError("exclude_sponsors must be a boolean")
    provider = detect_provider(url)
    if provider == Provider.YOUTUBE:
        from obsidian_ingest.extractor.providers.youtube import get_youtube_content

        categories = ("sponsor",) if exclude_sponsors else ()
        metadata, transcript, lookup = get_youtube_content(url, categories)
        filtered = filter_source_transcript(metadata, transcript, lookup)
        return Source(metadata, transcript, lookup, filtered)
    raise ValueError(f"Source ingestion is not supported for provider: {provider}")


def get_source_overview(source: Source) -> SourceOverview:
    """Expose normalized source context and the SponsorBlock outcome without transcript text."""
    return SourceOverview(
        metadata=source.metadata,
        content_category=categorize_content(source.metadata),
        chapter_count=len(source.metadata.chapters),
        exclusion_lookup=source.exclusion_lookup,
        original_segment_count=len(source.transcript.segments),
        retained_segment_count=len(source.filtered_transcript.retained_indices),
        excluded_segment_count=len(source.filtered_transcript.excluded),
    )


def list_chapters(source: Source) -> tuple[Chapter, ...]:
    """Return unchanged creator chapters in their original order, or an empty tuple."""
    return source.metadata.chapters


def read_transcript_entries(
    source: Source,
    *,
    start: float | None = None,
    end: float | None = None,
    include_excluded: bool = False,
) -> tuple[tuple[int, TranscriptSegment], ...]:
    """Return source-indexed atoms from the retained view or original transcript.

    Time ranges are seconds with half-open [start, end) ownership by segment start.
    Segments crossing boundaries remain whole and unchanged.
    """
    for name, bound in (("start", start), ("end", end)):
        if bound is None:
            continue
        if isinstance(bound, bool) or not isinstance(bound, (int, float)):
            raise TypeError(f"{name} must be a number of seconds or None")
        if not isfinite(bound) or bound < 0:
            raise ValueError(f"{name} must be finite and nonnegative")
    if start is not None and end is not None and end < start:
        raise ValueError("end must not precede start")
    if not isinstance(include_excluded, bool):
        raise TypeError("include_excluded must be a boolean")

    indices = (
        range(len(source.transcript.segments))
        if include_excluded
        else source.filtered_transcript.retained_indices
    )
    segments = source.transcript.segments
    return tuple(
        (index, segments[index])
        for index in indices
        if (start is None or segments[index].start >= start)
        and (end is None or segments[index].start < end)
    )


def read_transcript(
    source: Source,
    *,
    start: float | None = None,
    end: float | None = None,
    include_excluded: bool = False,
) -> tuple[TranscriptSegment, ...]:
    """Read retained transcript atoms by default; opt in to the original view explicitly."""
    if not isinstance(include_excluded, bool):
        raise TypeError("include_excluded must be a boolean")
    if (
        start is None
        and end is None
        and not include_excluded
        and source.filtered_transcript.retained_indices
        == tuple(range(len(source.transcript.segments)))
    ):
        return source.transcript.segments
    return tuple(
        segment
        for _, segment in read_transcript_entries(
            source, start=start, end=end, include_excluded=include_excluded
        )
    )


def read_chapter_entries(
    source: Source, chapter_index: int, *, include_excluded: bool = False
) -> tuple[tuple[int, TranscriptSegment], ...]:
    """Read source-indexed atoms in a zero-based creator chapter."""
    if isinstance(chapter_index, bool) or not isinstance(chapter_index, int):
        raise TypeError("chapter_index must be an integer")
    chapters = list_chapters(source)
    if not 0 <= chapter_index < len(chapters):
        raise IndexError(f"chapter_index {chapter_index} out of range for {len(chapters)} chapters")
    chapter = chapters[chapter_index]
    return read_transcript_entries(
        source,
        start=chapter.start,
        end=chapter.end,
        include_excluded=include_excluded,
    )


def read_chapter(
    source: Source, chapter_index: int, *, include_excluded: bool = False
) -> tuple[TranscriptSegment, ...]:
    """Read creator-chapter atoms from the retained view or original transcript."""
    return tuple(
        segment
        for _, segment in read_chapter_entries(
            source, chapter_index, include_excluded=include_excluded
        )
    )
