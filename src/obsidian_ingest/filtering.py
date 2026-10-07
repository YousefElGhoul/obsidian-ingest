"""Sponsor-range filtering that preserves original transcript atoms and provenance."""

from dataclasses import dataclass
from math import isfinite

from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
    SourceMetadata,
)
from obsidian_ingest.extractor.transcript import Transcript


@dataclass(frozen=True, slots=True)
class ExcludedSegment:
    source_index: int
    range_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FilteredTranscript:
    original: Transcript
    ranges: tuple[ExcludedRange, ...]
    retained_indices: tuple[int, ...]
    excluded: tuple[ExcludedSegment, ...]


def filter_transcript(
    transcript: Transcript, ranges: tuple[ExcludedRange, ...]
) -> FilteredTranscript:
    """Exclude atoms by start-time membership in half-open ranges, without editing atoms."""
    for index, exclusion in enumerate(ranges):
        if (
            isinstance(exclusion.start, bool)
            or not isinstance(exclusion.start, (int, float))
            or isinstance(exclusion.end, bool)
            or not isinstance(exclusion.end, (int, float))
            or not isfinite(exclusion.start)
            or not isfinite(exclusion.end)
            or not 0 <= exclusion.start < exclusion.end
            or not isinstance(exclusion.reason, str)
            or not exclusion.reason
            or not isinstance(exclusion.provenance, str)
            or not exclusion.provenance
        ):
            raise ValueError(f"Invalid exclusion range {index}")

    retained = []
    excluded = []
    previous_start = -1.0
    for index, segment in enumerate(transcript.segments):
        if (
            isinstance(segment.start, bool)
            or not isinstance(segment.start, (int, float))
            or isinstance(segment.duration, bool)
            or not isinstance(segment.duration, (int, float))
            or not isfinite(segment.start)
            or not isfinite(segment.duration)
            or segment.start < 0
            or segment.duration < 0
            or not isfinite(segment.start + segment.duration)
            or segment.start < previous_start
        ):
            raise ValueError(f"Invalid or unordered transcript timing at segment {index}")
        previous_start = segment.start
        matches = tuple(
            range_index
            for range_index, exclusion in enumerate(ranges)
            if exclusion.start <= segment.start < exclusion.end
        )
        if matches:
            excluded.append(ExcludedSegment(index, matches))
        else:
            retained.append(index)
    return FilteredTranscript(transcript, ranges, tuple(retained), tuple(excluded))


def validate_chapters(chapters: tuple[Chapter, ...]) -> None:
    """Validate creator chapter order and intervals without partitioning transcript data."""
    previous_end = 0.0
    for index, chapter in enumerate(chapters):
        if (
            isinstance(chapter.start, bool)
            or not isinstance(chapter.start, (int, float))
            or isinstance(chapter.end, bool)
            or not isinstance(chapter.end, (int, float))
            or not isfinite(chapter.start)
            or not isfinite(chapter.end)
            or not 0 <= chapter.start < chapter.end
            or chapter.start < previous_end
        ):
            raise ValueError(f"Invalid, unordered, or overlapping chapter {index}")
        previous_end = chapter.end


def filter_source_transcript(
    metadata: SourceMetadata, transcript: Transcript, lookup: ExclusionLookup
) -> FilteredTranscript:
    """Validate source identity/lookup consistency and build its retained view."""
    if metadata.source_id != transcript.video_id:
        raise ValueError("Metadata and transcript source identities do not match")
    if lookup.status == ExclusionStatus.DISABLED and lookup.ranges:
        raise ValueError("Disabled exclusion lookup cannot contain ranges")
    if lookup.status == ExclusionStatus.FAILED and lookup.ranges:
        raise ValueError("Failed exclusion lookup cannot contain ranges")
    if lookup.status == ExclusionStatus.FAILED and not lookup.error_message:
        raise ValueError("Failed exclusion lookup must contain an error message")
    validate_chapters(metadata.chapters)
    return filter_transcript(transcript, lookup.ranges)
