"""Deterministic baseline inspection, not model-validated semantic segmentation."""

from dataclasses import dataclass
from math import isfinite

from obsidian_ingest.extractor import ContentCategory, Provider, detect_provider
from obsidian_ingest.extractor.metadata import (
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
    SourceMetadata,
    categorize_content,
)
from obsidian_ingest.extractor.transcript import Transcript, format_timestamp


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


@dataclass(frozen=True, slots=True)
class BaselineChunk:
    # Half-open positions in retained_indices, never slices of original segments.
    retained_start: int
    retained_end: int
    chapter_index: int | None


@dataclass(frozen=True, slots=True)
class SegmentationResult:
    metadata: SourceMetadata
    filtered: FilteredTranscript
    lookup: ExclusionLookup
    chunks: tuple[BaselineChunk, ...]


def filter_transcript(
    transcript: Transcript, ranges: tuple[ExcludedRange, ...]
) -> FilteredTranscript:
    for index, exclusion in enumerate(ranges):
        if (
            not isfinite(exclusion.start)
            or not isfinite(exclusion.end)
            or not 0 <= exclusion.start < exclusion.end
            or not exclusion.reason
            or not exclusion.provenance
        ):
            raise ValueError(f"Invalid exclusion range {index}")
    retained = []
    excluded = []
    previous_start = -1.0
    for index, segment in enumerate(transcript.segments):
        if (
            not isfinite(segment.start)
            or not isfinite(segment.duration)
            or segment.start < 0
            or segment.duration < 0
            or not isfinite(segment.start + segment.duration)
            or segment.start < previous_start
        ):
            raise ValueError(f"Invalid or unordered transcript timing at segment {index}")
        previous_start = segment.start
        matches = tuple(
            i
            for i, exclusion in enumerate(ranges)
            if exclusion.start <= segment.start < exclusion.end
        )
        if matches:
            excluded.append(ExcludedSegment(index, matches))
        else:
            retained.append(index)
    return FilteredTranscript(transcript, ranges, tuple(retained), tuple(excluded))


def partition_sections(
    metadata: SourceMetadata, filtered: FilteredTranscript
) -> tuple[BaselineChunk, ...]:
    previous_end = 0.0
    for index, chapter in enumerate(metadata.chapters):
        if (
            not isfinite(chapter.start)
            or not isfinite(chapter.end)
            or not 0 <= chapter.start < chapter.end
            or chapter.start < previous_end
        ):
            raise ValueError(f"Invalid, unordered, or overlapping chapter {index}")
        previous_end = chapter.end

    sections = []
    chapter_position = 0
    previous_zone = None
    for position, source_index in enumerate(filtered.retained_indices):
        start = filtered.original.segments[source_index].start
        while (
            chapter_position < len(metadata.chapters)
            and start >= metadata.chapters[chapter_position].end
        ):
            chapter_position += 1
        chapter_index = None
        if (
            chapter_position < len(metadata.chapters)
            and start >= metadata.chapters[chapter_position].start
        ):
            chapter_index = chapter_position
        # Distinguish separate unchaptered gaps even if an intervening chapter is empty.
        zone = (chapter_position, chapter_index)
        if zone != previous_zone:
            if sections:
                last = sections[-1]
                sections[-1] = BaselineChunk(last.retained_start, position, last.chapter_index)
            sections.append(BaselineChunk(position, len(filtered.retained_indices), chapter_index))
            previous_zone = zone
    return tuple(sections)


def build_baseline(
    metadata: SourceMetadata, transcript: Transcript, lookup: ExclusionLookup
) -> SegmentationResult:
    if metadata.source_id != transcript.video_id:
        raise ValueError("Metadata and transcript source identities do not match")
    if lookup.status == ExclusionStatus.DISABLED and lookup.ranges:
        raise ValueError("Disabled exclusion lookup cannot contain ranges")
    filtered = filter_transcript(transcript, lookup.ranges)
    sections = partition_sections(metadata, filtered)
    category = categorize_content(metadata)
    if category in {ContentCategory.CLIP, ContentCategory.SHORT_FORM} and filtered.retained_indices:
        chunks = (BaselineChunk(0, len(filtered.retained_indices), None),)
    else:
        chunks = sections
    return SegmentationResult(metadata, filtered, lookup, chunks)


def inspect_source(url: str) -> SegmentationResult:
    if detect_provider(url) != Provider.YOUTUBE:
        raise ValueError("Source inspection is currently supported only for YouTube")
    from obsidian_ingest.extractor.providers.youtube import get_youtube_source

    return build_baseline(*get_youtube_source(url))


def _timestamp(seconds: float) -> str:
    # Millisecond display avoids hiding the half-open membership policy at endpoints.
    milliseconds = round(seconds * 1000)
    return f"{format_timestamp(milliseconds // 1000)}.{milliseconds % 1000:03}"


def _index_runs(indices: tuple[int, ...]) -> str:
    if not indices:
        return "none"
    runs = []
    first = last = indices[0]
    for index in indices[1:]:
        if index != last + 1:
            runs.append(str(first) if first == last else f"{first}-{last}")
            first = index
        last = index
    runs.append(str(first) if first == last else f"{first}-{last}")
    return ", ".join(runs)


def format_segmentation(result: SegmentationResult) -> str:
    metadata, filtered = result.metadata, result.filtered
    original = filtered.original.segments
    category = categorize_content(metadata)
    lines = [
        f"Source: {metadata.original_url}",
        f"Title: {metadata.title or '(unknown)'}",
        f"Category: {category or 'unknown'}",
        "Baseline chunks (no semantic model used)",
        "Source segment indices are zero-based; ranges below are inclusive index runs.",
        (
            f"Segments: {len(original)} original, {len(filtered.retained_indices)} retained, "
            f"{len(filtered.excluded)} excluded"
        ),
        f"Exclusion lookup: {result.lookup.provenance} / {result.lookup.status}",
        f"Selected categories: {', '.join(result.lookup.categories) or 'none'}",
    ]
    if result.lookup.retrieved_at is not None:
        lines.append(f"Lookup time: {result.lookup.retrieved_at.isoformat()}")
    lines.extend(["", "Excluded ranges (original timeline):"])
    for i, exclusion in enumerate(filtered.ranges):
        indices = tuple(item.source_index for item in filtered.excluded if i in item.range_indices)
        lines.append(
            f"Range {i}: [{_timestamp(exclusion.start)}-{_timestamp(exclusion.end)}) "
            f"{exclusion.reason} ({exclusion.provenance}); excluded indices: {_index_runs(indices)}"
        )
    if not filtered.ranges:
        lines.append("None.")
    if metadata.chapters:
        lines.extend(["", "Creator chapters (unchanged):"])
        for i, chapter in enumerate(metadata.chapters):
            count = sum(
                chapter.start <= original[index].start < chapter.end
                for index in filtered.retained_indices
            )
            lines.append(
                f"Chapter {i}: {chapter.title} [{_timestamp(chapter.start)}-"
                f"{_timestamp(chapter.end)}); {count} retained segments"
            )
    lines.extend(["", "Atomic exclusion-boundary crossings (text is never trimmed):"])
    crossings = []
    retained_set = set(filtered.retained_indices)
    for index, segment in enumerate(original):
        end = segment.start + segment.duration
        for i, exclusion in enumerate(filtered.ranges):
            if segment.start < exclusion.start < end or segment.start < exclusion.end < end:
                state = "retained" if index in retained_set else "excluded"
                crossings.append(f"Segment {index}: {state}, crosses range {i}")
    lines.extend(crossings or ["None."])
    for i, chunk in enumerate(result.chunks):
        indices = filtered.retained_indices[chunk.retained_start : chunk.retained_end]
        start = original[indices[0]].start
        end = max(original[index].start + original[index].duration for index in indices)
        chapter_label = (
            f"{chunk.chapter_index}: {metadata.chapters[chunk.chapter_index].title}"
            if chunk.chapter_index is not None
            else "none (whole source or unchaptered section)"
        )
        lines.extend(
            [
                "",
                f"Chunk {i}: [{_timestamp(start)}-{_timestamp(end)}]",
                f"Chapter: {chapter_label}",
                f"Source indices: {_index_runs(indices)}",
            ]
        )
        for index in indices:
            segment = original[index]
            lines.append(f"[{index}] [{_timestamp(segment.start)}] {segment.text}")
    if not result.chunks:
        lines.extend(["", "No retained chunks (empty transcript or all segments excluded)."])
    return "\n".join(lines)
