from dataclasses import replace
from datetime import UTC, datetime

import pytest

from obsidian_ingest.extractor import ContentCategory
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
    NativeFormat,
    SourceMetadata,
    categorize_content,
)
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.segmentation import (
    BaselineChunk,
    ExcludedSegment,
    build_baseline,
    filter_transcript,
    format_segmentation,
    partition_sections,
)


@pytest.fixture
def metadata() -> SourceMetadata:
    return normalize_youtube_metadata(
        {"id": "abc", "title": "Lesson", "duration": 600}, "https://youtu.be/abc"
    )


def transcript(*segments: TranscriptSegment) -> Transcript:
    return Transcript("abc", "en", "en", True, segments)


def lookup(*ranges: ExcludedRange) -> ExclusionLookup:
    return ExclusionLookup(ranges, ExclusionStatus.COMPLETE, "SponsorBlock", ("sponsor",))


def test_filter_uses_original_indices_and_half_open_starts_not_overlap() -> None:
    original = transcript(
        TranscriptSegment("crosses into sponsor", 9, 3),
        TranscriptSegment("at start", 10, 0),
        TranscriptSegment("both ranges", 15, 1),
        TranscriptSegment("crosses out", 19, 6),
        TranscriptSegment("at first end", 20, 1),
        TranscriptSegment("at final end", 22, 1),
    )
    ranges = (
        ExcludedRange(10, 20, "sponsor", "SponsorBlock"),
        ExcludedRange(15, 22, "sponsor", "second vote"),
    )
    result = filter_transcript(original, ranges)

    assert result.original is original
    assert result.ranges is ranges
    assert result.retained_indices == (0, 5)
    assert result.excluded == (
        ExcludedSegment(1, (0,)),
        ExcludedSegment(2, (0, 1)),
        ExcludedSegment(3, (0, 1)),
        ExcludedSegment(4, (1,)),
    )
    assert result.original.segments is original.segments
    assert [(s.text, s.start, s.duration) for s in original.segments] == [
        ("crosses into sponsor", 9, 3),
        ("at start", 10, 0),
        ("both ranges", 15, 1),
        ("crosses out", 19, 6),
        ("at first end", 20, 1),
        ("at final end", 22, 1),
    ]


def test_cross_chapter_and_internal_exclusions_do_not_create_chapters(
    metadata: SourceMetadata,
) -> None:
    metadata = replace(metadata, chapters=(Chapter("A", 0, 10), Chapter("B", 10, 20)))
    original = transcript(*(TranscriptSegment(str(i), i, 3) for i in range(20)))
    result = build_baseline(
        metadata,
        original,
        lookup(
            ExcludedRange(3, 5, "sponsor", "SponsorBlock"),
            ExcludedRange(8, 12, "sponsor", "SponsorBlock"),
        ),
    )

    assert result.metadata is metadata
    assert result.metadata.chapters is metadata.chapters
    assert result.chunks == (BaselineChunk(0, 6, 0), BaselineChunk(6, 14, 1))
    covered = tuple(
        index
        for chunk in result.chunks
        for index in result.filtered.retained_indices[chunk.retained_start : chunk.retained_end]
    )
    excluded = tuple(item.source_index for item in result.filtered.excluded)
    assert covered == (0, 1, 2, 5, 6, 7, 12, 13, 14, 15, 16, 17, 18, 19)
    assert excluded == (3, 4, 8, 9, 10, 11)
    assert len(set(covered + excluded)) == len(original.segments)
    assert sorted(covered + excluded) == list(range(len(original.segments)))
    assert all(result.filtered.original.segments[i] is original.segments[i] for i in covered)


def test_gaps_remain_separate_across_entirely_excluded_chapter(
    metadata: SourceMetadata,
) -> None:
    metadata = replace(metadata, chapters=(Chapter("A", 2, 4), Chapter("Sponsor", 6, 8)))
    original = transcript(*(TranscriptSegment(str(i), i, 1) for i in range(10)))
    result = build_baseline(
        metadata, original, lookup(ExcludedRange(6, 8, "sponsor", "SponsorBlock"))
    )

    assert partition_sections(metadata, result.filtered) == (
        BaselineChunk(0, 2, None),
        BaselineChunk(2, 4, 0),
        BaselineChunk(4, 6, None),
        BaselineChunk(6, 8, None),
    )
    assert result.chunks == partition_sections(metadata, result.filtered)
    assert "Chapter 1: Sponsor [00:06.000-00:08.000); 0 retained segments" in (
        format_segmentation(result)
    )


@pytest.mark.parametrize(
    ("duration", "native_format", "category", "whole"),
    [
        (600, NativeFormat.VERTICAL_SHORT, ContentCategory.CLIP, True),
        (479, NativeFormat.STANDARD, ContentCategory.SHORT_FORM, True),
        (480, NativeFormat.STANDARD, ContentCategory.LONG_FORM, False),
        (3000, NativeFormat.STANDARD, ContentCategory.LONG_FORM, False),
        (3001, NativeFormat.STANDARD, ContentCategory.EXTENDED, False),
        (None, NativeFormat.STANDARD, None, False),
    ],
)
def test_categories_choose_whole_source_or_chapter_first(
    metadata: SourceMetadata,
    duration: float | None,
    native_format: NativeFormat,
    category: ContentCategory | None,
    whole: bool,
) -> None:
    metadata = replace(
        metadata,
        duration_seconds=duration,
        native_format=native_format,
        chapters=(Chapter("A", 0, 10), Chapter("B", 10, 20)),
    )
    result = build_baseline(
        metadata,
        transcript(TranscriptSegment("A", 0, 15), TranscriptSegment("B", 10, 1)),
        lookup(),
    )
    assert categorize_content(metadata) == category
    assert result.chunks == (
        (BaselineChunk(0, 2, None),) if whole else (BaselineChunk(0, 1, 0), BaselineChunk(1, 2, 1))
    )


@pytest.mark.parametrize("duration", [60, 600, None])
def test_no_chapters_is_one_whole_chunk(metadata: SourceMetadata, duration: float | None) -> None:
    result = build_baseline(
        replace(metadata, duration_seconds=duration),
        transcript(TranscriptSegment("A", 0, 1), TranscriptSegment("B", 5, 1)),
        lookup(),
    )
    assert result.chunks == (BaselineChunk(0, 2, None),)


@pytest.mark.parametrize("all_excluded", [False, True])
def test_empty_retained_transcript_has_no_chunks(
    metadata: SourceMetadata, all_excluded: bool
) -> None:
    original = (
        transcript(TranscriptSegment("discarded sponsor", 1, 1)) if all_excluded else transcript()
    )
    result = build_baseline(
        replace(metadata, chapters=(Chapter("Sponsor", 0, 3),)),
        original,
        lookup(ExcludedRange(0, 3, "sponsor", "SponsorBlock")),
    )
    assert result.filtered.retained_indices == ()
    assert result.chunks == ()
    report = format_segmentation(result)
    assert "No retained chunks (empty transcript or all segments excluded)." in report
    assert "Chapter 0: Sponsor [00:00.000-00:03.000); 0 retained segments" in report
    assert "discarded sponsor" not in report


@pytest.mark.parametrize(
    "chapters",
    [
        (Chapter("bad", -1, 2),),
        (Chapter("bad", 2, 2),),
        (Chapter("bad", 3, 2),),
        (Chapter("bad", float("nan"), 2),),
        (Chapter("bad", 0, float("inf")),),
        (Chapter("first", 0, 5), Chapter("overlap", 4, 8)),
        (Chapter("first", 5, 8), Chapter("unordered", 0, 3)),
    ],
)
def test_invalid_chapters_fail_even_with_empty_transcript(
    metadata: SourceMetadata, chapters: tuple[Chapter, ...]
) -> None:
    with pytest.raises(ValueError, match="Invalid, unordered, or overlapping chapter [01]"):
        partition_sections(
            replace(metadata, chapters=chapters), filter_transcript(transcript(), ())
        )


@pytest.mark.parametrize(
    ("start", "duration"),
    [
        (-1, 1),
        (0, -1),
        (float("nan"), 1),
        (float("inf"), 1),
        (0, float("nan")),
        (0, float("inf")),
        (1e308, 1e308),
    ],
)
def test_invalid_transcript_timing_is_rejected(start: float, duration: float) -> None:
    with pytest.raises(ValueError, match="Invalid or unordered transcript timing at segment 0"):
        filter_transcript(transcript(TranscriptSegment("bad", start, duration)), ())


def test_unordered_transcript_is_rejected_but_equal_starts_are_allowed() -> None:
    first = TranscriptSegment("first", 2, 0)
    assert filter_transcript(transcript(first, first), ()).retained_indices == (0, 1)
    with pytest.raises(ValueError, match="Invalid or unordered transcript timing at segment 1"):
        filter_transcript(transcript(first, TranscriptSegment("earlier", 1, 1)), ())


@pytest.mark.parametrize(
    ("start", "end", "reason", "provenance"),
    [
        (-1, 2, "sponsor", "SponsorBlock"),
        (2, 2, "sponsor", "SponsorBlock"),
        (3, 2, "sponsor", "SponsorBlock"),
        (float("nan"), 2, "sponsor", "SponsorBlock"),
        (0, float("inf"), "sponsor", "SponsorBlock"),
        (0, 2, "", "SponsorBlock"),
        (0, 2, "sponsor", ""),
    ],
)
def test_invalid_ranges_fail_even_with_empty_transcript(
    start: float, end: float, reason: str, provenance: str
) -> None:
    with pytest.raises(ValueError, match="Invalid exclusion range 0"):
        filter_transcript(transcript(), (ExcludedRange(start, end, reason, provenance),))


def test_unordered_overlapping_ranges_keep_original_attribution() -> None:
    result = filter_transcript(
        transcript(TranscriptSegment("sponsor", 6, 1)),
        (ExcludedRange(5, 10, "sponsor", "first"), ExcludedRange(0, 8, "sponsor", "second")),
    )
    assert result.excluded == (ExcludedSegment(0, (0, 1)),)


def test_source_identity_mismatch_and_disabled_ranges_fail(metadata: SourceMetadata) -> None:
    with pytest.raises(ValueError, match="Metadata and transcript source identities do not match"):
        build_baseline(metadata, replace(transcript(), video_id="other"), lookup())
    exclusions = replace(
        lookup(ExcludedRange(0, 1, "sponsor", "SponsorBlock")), status=ExclusionStatus.DISABLED
    )
    with pytest.raises(ValueError, match="Disabled exclusion lookup cannot contain ranges"):
        build_baseline(metadata, transcript(), exclusions)
    assert build_baseline(metadata, transcript(), replace(exclusions, ranges=())).chunks == ()


def test_report_snapshot_preserves_precision_crossings_and_maximum_caption_end(
    metadata: SourceMetadata,
) -> None:
    result = build_baseline(
        metadata,
        transcript(
            TranscriptSegment("Original  v1.2 wording", 1.125, 30),
            TranscriptSegment("DISCARDED SPONSOR", 2.25, 2),
            TranscriptSegment("After", 4.125, 1),
        ),
        replace(
            lookup(ExcludedRange(2.25, 4.125, "sponsor", "SponsorBlock")),
            retrieved_at=datetime(2026, 10, 2, tzinfo=UTC),
        ),
    )
    report = format_segmentation(result)
    assert "DISCARDED SPONSOR" not in report
    assert (
        report
        == """Source: https://youtu.be/abc
Title: Lesson
Category: long_form
Baseline chunks (no semantic model used)
Source segment indices are zero-based; ranges below are inclusive index runs.
Segments: 3 original, 2 retained, 1 excluded
Exclusion lookup: SponsorBlock / complete
Selected categories: sponsor
Lookup time: 2026-10-02T00:00:00+00:00

Excluded ranges (original timeline):
Range 0: [00:02.250-00:04.125) sponsor (SponsorBlock); excluded indices: 1

Atomic exclusion-boundary crossings (text is never trimmed):
Segment 0: retained, crosses range 0
Segment 1: excluded, crosses range 0

Chunk 0: [00:01.125-00:31.125]
Chapter: none (whole source or unchaptered section)
Source indices: 0, 2
[0] [00:01.125] Original  v1.2 wording
[2] [00:04.125] After"""
    )


def test_report_attributes_overlapping_ranges_and_formats_index_runs(
    metadata: SourceMetadata,
) -> None:
    result = build_baseline(
        metadata,
        transcript(*(TranscriptSegment(f"text {i}", i, 1) for i in range(6))),
        lookup(
            ExcludedRange(1, 4, "sponsor", "first"),
            ExcludedRange(2, 3, "sponsor", "second"),
            ExcludedRange(10, 11, "sponsor", "unused"),
        ),
    )
    report = format_segmentation(result)
    assert "Range 0: [00:01.000-00:04.000) sponsor (first); excluded indices: 1-3" in report
    assert "Range 1: [00:02.000-00:03.000) sponsor (second); excluded indices: 2" in report
    assert "Range 2: [00:10.000-00:11.000) sponsor (unused); excluded indices: none" in report
    assert "Source indices: 0, 4-5" in report
    assert "Atomic exclusion-boundary crossings (text is never trimmed):\nNone." in report
    assert all(f"text {i}" not in report for i in (1, 2, 3))


def test_thousands_of_atoms_have_complete_ordered_coverage(metadata: SourceMetadata) -> None:
    original = transcript(*(TranscriptSegment(str(i), i, 1) for i in range(6000)))
    metadata = replace(
        metadata,
        duration_seconds=6000,
        chapters=tuple(Chapter(f"Chapter {i}", i * 600, (i + 1) * 600) for i in range(10)),
    )
    result = build_baseline(
        metadata,
        original,
        lookup(ExcludedRange(550, 650, "sponsor", "SponsorBlock")),
    )
    covered = tuple(
        index
        for chunk in result.chunks
        for index in result.filtered.retained_indices[chunk.retained_start : chunk.retained_end]
    )
    assert covered == result.filtered.retained_indices
    assert len(covered) == 5900
    assert len(set(covered)) == len(covered)
    assert sorted((*covered, *(item.source_index for item in result.filtered.excluded))) == list(
        range(6000)
    )
