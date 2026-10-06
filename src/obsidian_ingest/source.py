"""Normalized source ingestion and read-only navigation, independent of adapters."""

from dataclasses import dataclass
from math import isfinite

from obsidian_ingest.extractor import ContentCategory, Provider, detect_provider
from obsidian_ingest.extractor.metadata import Chapter, SourceMetadata, categorize_content
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment


@dataclass(frozen=True, slots=True)
class Source:
    metadata: SourceMetadata
    transcript: Transcript


@dataclass(frozen=True, slots=True)
class SourceOverview:
    metadata: SourceMetadata
    content_category: ContentCategory | None
    chapter_count: int


def ingest_source(url: str) -> Source:
    """Fetch metadata and original captions together, without SponsorBlock lookup."""
    provider = detect_provider(url)
    if provider == Provider.YOUTUBE:
        from obsidian_ingest.extractor.providers.youtube import get_youtube_content

        metadata, transcript = get_youtube_content(url)
        return Source(metadata, transcript)
    raise ValueError(f"Source ingestion is not supported for provider: {provider}")


def get_source_overview(source: Source) -> SourceOverview:
    """Expose normalized source context without reading transcript text or doing I/O."""
    return SourceOverview(
        source.metadata, categorize_content(source.metadata), len(source.metadata.chapters)
    )


def list_chapters(source: Source) -> tuple[Chapter, ...]:
    """Return unchanged creator chapters in their original order, or an empty tuple."""
    return source.metadata.chapters


def read_transcript(
    source: Source, *, start: float | None = None, end: float | None = None
) -> tuple[TranscriptSegment, ...]:
    """Return original atoms whose start times fall in [start, end), in seconds.

    None leaves that side unbounded. Bounds must be finite, nonnegative numbers;
    end must not precede start. Equal bounds or a range outside the transcript
    return an empty tuple. Crossing atoms are never split, trimmed, or duplicated.
    No reads perform I/O or alter text, timestamps, durations, or source order.
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
    if start is None and end is None:
        return source.transcript.segments
    return tuple(
        segment
        for segment in source.transcript.segments
        if (start is None or segment.start >= start) and (end is None or segment.start < end)
    )


def read_chapter(source: Source, chapter_index: int) -> tuple[TranscriptSegment, ...]:
    """Read a zero-based creator chapter with the same start-time ownership rule."""
    if isinstance(chapter_index, bool) or not isinstance(chapter_index, int):
        raise TypeError("chapter_index must be an integer")
    chapters = list_chapters(source)
    if not 0 <= chapter_index < len(chapters):
        raise IndexError(f"chapter_index {chapter_index} out of range for {len(chapters)} chapters")
    chapter = chapters[chapter_index]
    return read_transcript(source, start=chapter.start, end=chapter.end)
