from dataclasses import replace

import pytest

from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
    SourceMetadata,
)
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import (
    ExcludedSegment,
    filter_source_transcript,
    filter_transcript,
    validate_chapters,
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
    assert [segment.text for segment in original.segments] == [
        "crosses into sponsor",
        "at start",
        "both ranges",
        "crosses out",
        "at first end",
        "at final end",
    ]


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
        (True, 1),
        (0, False),
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
        (True, 2, "sponsor", "SponsorBlock"),
        (0, False, "sponsor", "SponsorBlock"),
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


def test_large_transcript_filter_has_complete_ordered_source_coverage() -> None:
    original = transcript(*(TranscriptSegment(str(index), index, 1) for index in range(6000)))
    result = filter_transcript(original, (ExcludedRange(550, 650, "sponsor", "SponsorBlock"),))
    retained = result.retained_indices
    excluded = tuple(segment.source_index for segment in result.excluded)

    assert len(retained) == 5900
    assert len(excluded) == 100
    assert len(set(retained + excluded)) == len(original.segments)
    assert sorted((*retained, *excluded)) == list(range(len(original.segments)))
    assert all(original.segments[index].text == str(index) for index in retained)


def test_identity_lookup_and_chapter_validation(metadata: SourceMetadata) -> None:
    completed = lookup()
    with pytest.raises(ValueError, match="source identities do not match"):
        filter_source_transcript(metadata, replace(transcript(), video_id="other"), completed)
    disabled_with_ranges = replace(
        lookup(ExcludedRange(0, 1, "sponsor", "SponsorBlock")), status=ExclusionStatus.DISABLED
    )
    with pytest.raises(ValueError, match="Disabled exclusion lookup cannot contain ranges"):
        filter_source_transcript(metadata, transcript(), disabled_with_ranges)
    failed_with_ranges = replace(disabled_with_ranges, status=ExclusionStatus.FAILED)
    with pytest.raises(ValueError, match="Failed exclusion lookup cannot contain ranges"):
        filter_source_transcript(metadata, transcript(), failed_with_ranges)
    original = transcript(TranscriptSegment("keep all on failed lookup", 0, 1))
    failed = replace(
        lookup(),
        status=ExclusionStatus.FAILED,
        ranges=(),
        error_message="lookup unavailable",
    )
    filtered = filter_source_transcript(metadata, original, failed)
    assert filtered.original is original
    assert filtered.retained_indices == (0,)


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
        (Chapter("bad", True, 2),),
    ],
)
def test_invalid_chapters_are_rejected_even_without_transcript_atoms(
    chapters: tuple[Chapter, ...],
) -> None:
    with pytest.raises(ValueError, match="Invalid, unordered, or overlapping chapter"):
        validate_chapters(chapters)
