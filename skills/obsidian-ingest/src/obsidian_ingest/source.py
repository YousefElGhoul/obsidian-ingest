"""Normalized source ingestion and read-only navigation, independent of adapters."""

from dataclasses import dataclass, field
from math import isfinite

from obsidian_ingest.diagnostics import TimingObservations, observe_timing
from obsidian_ingest.extractor import ContentCategory, SourceKind
from obsidian_ingest.extractor.acquisition import Acquisition, OriginalCaption
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExclusionLookup,
    SourceMetadata,
    categorize_content,
)
from obsidian_ingest.extractor.providers import ProviderContent, supported_provider
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import FilteredTranscript, filter_source_transcript


@dataclass(frozen=True, slots=True)
class Source:
    """One capture. ``acquisition`` is None only when provenance was never recorded.

    ``original_caption`` carries the raw response from a fresh acquisition to the cache;
    it is not loaded on cache reads and does not take part in equality.
    """

    metadata: SourceMetadata
    transcript: Transcript
    exclusion_lookup: ExclusionLookup
    filtered_transcript: FilteredTranscript
    acquisition: Acquisition | None = None
    original_caption: OriginalCaption | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class SourceOverview:
    """Summarize acquired evidence and navigation counts for agent-led curation."""

    metadata: SourceMetadata
    content_category: ContentCategory | None
    chapter_count: int
    exclusion_lookup: ExclusionLookup
    original_segment_count: int
    retained_segment_count: int
    excluded_segment_count: int
    caption_language: str
    caption_generated: bool
    acquisition: Acquisition | None
    timing: TimingObservations | None


def ingest_source(url: str, *, exclude_sponsors: bool = True) -> Source:
    """Fetch one normalized source; exclusion filtering is best-effort and on by default."""
    if not isinstance(exclude_sponsors, bool):
        raise TypeError("exclude_sponsors must be a boolean")
    content = supported_provider(url).acquire(url, exclude_sponsors=exclude_sponsors)
    return source_from_content(content)


def source_from_content(content: ProviderContent) -> Source:
    """Assemble a validated Source, with its filtered view, from one provider capture."""
    filtered = filter_source_transcript(content.metadata, content.transcript, content.exclusions)
    return Source(
        content.metadata,
        content.transcript,
        content.exclusions,
        filtered,
        content.acquisition,
        content.original,
    )


def get_source_overview(source: Source) -> SourceOverview:
    """Expose normalized source context, filtering outcome, and caption observations."""
    return SourceOverview(
        metadata=source.metadata,
        content_category=categorize_content(source.metadata),
        chapter_count=len(source.metadata.chapters),
        exclusion_lookup=source.exclusion_lookup,
        original_segment_count=len(source.transcript.segments),
        retained_segment_count=len(source.filtered_transcript.retained_indices),
        excluded_segment_count=len(source.filtered_transcript.excluded),
        caption_language=source.transcript.language,
        caption_generated=source.transcript.is_generated,
        acquisition=source.acquisition,
        timing=(
            observe_timing(source.metadata, source.transcript)
            if source.transcript.kind == SourceKind.TIMED
            else None
        ),
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
    if source.transcript.kind == SourceKind.TEXT and (start is not None or end is not None):
        raise ValueError(
            "start and end are seconds and apply only to videos; read a text source in "
            "order, or one section with read_section"
        )

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
        raise IndexError(f"section_index {chapter_index} out of range for {len(chapters)} sections")
    chapter = chapters[chapter_index]
    if source.transcript.kind == SourceKind.TEXT:
        # Text sections are half-open source-index ranges, not time ranges.
        return tuple(
            entry
            for entry in read_transcript_entries(source, include_excluded=include_excluded)
            if chapter.start <= entry[0] < chapter.end
        )
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
