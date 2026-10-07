"""Human-readable source and SponsorBlock diagnostics without transcript partitioning."""

from obsidian_ingest.extractor.metadata import categorize_content
from obsidian_ingest.extractor.transcript import format_timestamp
from obsidian_ingest.filtering import ExcludedSegment
from obsidian_ingest.source import Source, ingest_source


def inspect_source(url: str) -> Source:
    """Ingest a source using the normal best-effort sponsor-filtering policy."""
    return ingest_source(url)


def _timestamp(seconds: float) -> str:
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


def _excluded_indices(excluded: tuple[ExcludedSegment, ...], range_index: int) -> tuple[int, ...]:
    return tuple(item.source_index for item in excluded if range_index in item.range_indices)


def format_source_inspection(source: Source) -> str:
    """Show the retained source view and filtering outcome, without creating chunks."""
    metadata = source.metadata
    lookup = source.exclusion_lookup
    filtered = source.filtered_transcript
    lines = [
        f"Source: {metadata.original_url}",
        f"Title: {metadata.title or '(unknown)'}",
        f"Creator: {metadata.creator_name or '(unknown)'}",
        f"Duration: {metadata.duration_seconds if metadata.duration_seconds is not None else 'unknown'}",
        f"Provider: {metadata.provider}",
        f"Content category: {categorize_content(metadata) or 'unknown'}",
        f"Creator chapters: {len(metadata.chapters)}",
        f"Sponsor filtering: {lookup.status.value}",
        f"Selected categories: {', '.join(lookup.categories) or 'none'}",
        (
            f"Segments: {len(source.transcript.segments)} original, "
            f"{len(filtered.retained_indices)} retained, {len(filtered.excluded)} excluded"
        ),
    ]
    if lookup.retrieved_at is not None:
        lines.append(f"Lookup time: {lookup.retrieved_at.isoformat()}")
    if lookup.error_message:
        lines.append(f"Sponsor filtering warning: {lookup.error_message}")

    lines.extend(["", "Sponsor ranges:"])
    if filtered.ranges:
        for range_index, exclusion in enumerate(filtered.ranges):
            lines.append(
                f"Range {range_index}: [{_timestamp(exclusion.start)}-{_timestamp(exclusion.end)}) "
                f"{exclusion.reason} ({exclusion.provenance}); excluded indices: "
                f"{_index_runs(_excluded_indices(filtered.excluded, range_index))}"
            )
    else:
        lines.append("None.")

    if metadata.chapters:
        lines.extend(["", "Creator chapters (unchanged):"])
        for index, chapter in enumerate(metadata.chapters):
            retained_count = sum(
                chapter.start <= source.transcript.segments[source_index].start < chapter.end
                for source_index in filtered.retained_indices
            )
            lines.append(
                f"Chapter {index}: {chapter.title} [{_timestamp(chapter.start)}-"
                f"{_timestamp(chapter.end)}); {retained_count} retained segments"
            )

    lines.extend(["", "Retained transcript atoms (not semantic chunks):"])
    for source_index in filtered.retained_indices:
        segment = source.transcript.segments[source_index]
        lines.append(f"[{source_index}] [{_timestamp(segment.start)}] {segment.text}")
    if not filtered.retained_indices:
        lines.append("No retained transcript atoms.")
    return "\n".join(lines)
