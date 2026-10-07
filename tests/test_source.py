import json
from dataclasses import FrozenInstanceError, replace
from unittest.mock import MagicMock

import pytest

from obsidian_ingest.extractor import ContentCategory, Provider
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
    NativeFormat,
)
from obsidian_ingest.extractor.providers import youtube
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata, sponsorblock
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.source import (
    Source,
    get_source_overview,
    ingest_source,
    list_chapters,
    read_chapter,
    read_transcript,
)

URL = "https://youtu.be/request-id"


@pytest.fixture
def source() -> Source:
    metadata = normalize_youtube_metadata(
        {
            "id": "snapshot-id",
            "title": "Source context",
            "description": "A useful explanation",
            "channel": "Creator",
            "duration": 600,
            "chapters": [
                {"title": "First", "start_time": 0, "end_time": 120},
                {"title": "Second", "start_time": 120, "end_time": 300},
                {"title": "Empty", "start_time": 400, "end_time": 500},
            ],
        },
        URL,
    )
    transcript = Transcript(
        "snapshot-id",
        "en-US",
        "en-US",
        False,
        (
            TranscriptSegment("  Original\nwording  ", 0, 1.25),
            TranscriptSegment("Crosses into the second chapter", 119.5, 2),
            TranscriptSegment("Exactly at the start", 120, 0.5),
            TranscriptSegment("Same timestamp, still in source order", 120, 1),
            TranscriptSegment("Crosses beyond the end", 299.75, 2),
            TranscriptSegment("Exactly at the end", 300, 1),
        ),
    )
    lookup = ExclusionLookup((), ExclusionStatus.DISABLED, "SponsorBlock", ())
    filtered = filter_source_transcript(metadata, transcript, lookup)
    return Source(metadata, transcript, lookup, filtered)


@pytest.fixture
def source_io(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, MagicMock]:
    factory = MagicMock()
    ydl = factory.return_value.__enter__.return_value
    ydl.extract_info.return_value = {
        "id": "snapshot-id",
        "webpage_url": "https://www.youtube.com/watch?v=snapshot-id",
        "title": "One snapshot",
        "description": "Source context",
        "channel": "Creator",
        "uploader_id": "@creator",
        "duration": 600,
        "media_type": "video",
        "chapters": [{"title": "Lesson", "start_time": 0, "end_time": 600}],
        "automatic_captions": {
            "en": [{"ext": "json3", "url": "https://example.com/translated"}],
            "en-orig": [{"ext": "json3", "url": "https://example.com/original"}],
        },
    }
    ydl.urlopen.return_value.read.return_value = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 125,
                    "dDurationMs": 2000,
                    "segs": [{"utf8": "Original "}, {"utf8": "words\nunchanged"}],
                },
                {"tStartMs": 2125, "dDurationMs": 750, "segs": [{"utf8": "Next"}]},
            ]
        }
    ).encode()
    exclusions = ExclusionLookup(
        (ExcludedRange(1, 2, "sponsor", "SponsorBlock"),),
        ExclusionStatus.COMPLETE,
        "SponsorBlock",
        ("sponsor",),
    )
    ydl.urlopen.return_value.read.return_value = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 125,
                    "dDurationMs": 2000,
                    "segs": [{"utf8": "Original "}, {"utf8": "words\nunchanged"}],
                },
                {"tStartMs": 1000, "dDurationMs": 500, "segs": [{"utf8": "Sponsor atom"}]},
                {"tStartMs": 2125, "dDurationMs": 750, "segs": [{"utf8": "Next"}]},
            ]
        }
    ).encode()
    fetch_exclusions = MagicMock(return_value=exclusions)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(sponsorblock, "fetch_exclusions", fetch_exclusions)
    return factory, ydl, fetch_exclusions


def test_ingest_source_reuses_one_snapshot_and_reads_do_not_extract_again(
    source_io: tuple[MagicMock, MagicMock, MagicMock],
) -> None:
    factory, ydl, fetch_exclusions = source_io
    info = ydl.extract_info.return_value
    source = ingest_source(URL)

    assert isinstance(source, Source)
    assert source.metadata.source_id == source.transcript.video_id == "snapshot-id"
    assert source.metadata.original_url == URL
    assert source.metadata.canonical_url == "https://www.youtube.com/watch?v=snapshot-id"
    assert source.metadata.title == "One snapshot"
    assert source.metadata.description == "Source context"
    assert source.metadata.creator_name == "Creator"
    assert source.metadata.provider == Provider.YOUTUBE
    assert source.metadata.duration_seconds == 600
    assert source.metadata.native_format == NativeFormat.STANDARD
    assert source.metadata.provider_details.channel_handle == "@creator"
    assert source.metadata.chapters == (Chapter("Lesson", 0, 600),)
    assert source.transcript == Transcript(
        "snapshot-id",
        "en",
        "en",
        True,
        (
            TranscriptSegment("Original words\nunchanged", 0.125, 2),
            TranscriptSegment("Sponsor atom", 1, 0.5),
            TranscriptSegment("Next", 2.125, 0.75),
        ),
    )
    assert source.exclusion_lookup is not None
    assert source.filtered_transcript.retained_indices == (0, 2)
    assert source.filtered_transcript.excluded[0].source_index == 1
    assert tuple(segment.text for segment in read_transcript(source)) == (
        "Original words\nunchanged",
        "Next",
    )
    assert tuple(segment.text for segment in read_transcript(source, include_excluded=True)) == (
        "Original words\nunchanged",
        "Sponsor atom",
        "Next",
    )
    assert tuple(segment.text for segment in read_chapter(source, 0)) == (
        "Original words\nunchanged",
        "Next",
    )
    get_source_overview(source)
    list_chapters(source)
    read_transcript(source)
    read_transcript(source, start=1, end=3)
    read_chapter(source, 0)
    factory.assert_called_once_with({"quiet": True, "no_warnings": True, "noplaylist": True})
    ydl.extract_info.assert_called_once_with(URL, download=False)
    ydl.urlopen.assert_called_once_with("https://example.com/original")
    fetch_exclusions.assert_called_once_with(ydl, info, ("sponsor",))
    with pytest.raises(FrozenInstanceError):
        source.transcript = source.transcript


@pytest.mark.parametrize("info", [None, {}, {"_type": "playlist"}, {"_type": "multi_video"}])
def test_ingestion_rejects_non_video_results_before_reading_captions(
    source_io: tuple[MagicMock, MagicMock, MagicMock], info: dict | None
) -> None:
    _, ydl, fetch_exclusions = source_io
    ydl.extract_info.return_value = info
    with pytest.raises(ValueError, match="single YouTube video"):
        ingest_source(URL)
    ydl.extract_info.assert_called_once_with(URL, download=False)
    ydl.urlopen.assert_not_called()
    fetch_exclusions.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/abc/",
        "https://vm.tiktok.com/abc/",
        "https://example.com/video",
        "https://youtube.com.evil.example/watch?v=abc",
    ],
)
def test_ingestion_rejects_unsupported_providers_without_io(
    source_io: tuple[MagicMock, MagicMock, MagicMock], url: str
) -> None:
    factory, _, _ = source_io
    with pytest.raises(ValueError):
        ingest_source(url)
    factory.assert_not_called()


def test_sponsor_lookup_failure_does_not_block_ingestion(
    source_io: tuple[MagicMock, MagicMock, MagicMock],
) -> None:
    _, ydl, fetch_exclusions = source_io
    ydl.extract_info.return_value.pop("duration")
    fetch_exclusions.side_effect = RuntimeError("SponsorBlock duration unavailable")
    source = ingest_source(URL)
    assert source.metadata.duration_seconds is None
    assert get_source_overview(source).content_category is None
    assert source.exclusion_lookup.status == ExclusionStatus.FAILED
    assert source.exclusion_lookup.error_message == "SponsorBlock duration unavailable"
    assert source.filtered_transcript.retained_indices == (0, 1, 2)
    assert tuple(segment.text for segment in read_transcript(source)) == tuple(
        segment.text for segment in source.transcript.segments
    )
    ydl.urlopen.assert_called_once_with("https://example.com/original")
    fetch_exclusions.assert_called_once()


def test_sponsor_filtering_can_be_disabled(source_io) -> None:
    _, ydl, fetch_exclusions = source_io
    disabled = ExclusionLookup((), ExclusionStatus.DISABLED, "SponsorBlock", ())
    fetch_exclusions.return_value = disabled

    source = ingest_source(URL, exclude_sponsors=False)

    fetch_exclusions.assert_called_once()
    assert fetch_exclusions.call_args.args[2] == ()
    assert source.exclusion_lookup is disabled
    assert source.filtered_transcript.retained_indices == (0, 1, 2)
    ydl.extract_info.assert_called_once_with(URL, download=False)


def test_ingestion_preserves_caption_selection_errors(
    source_io: tuple[MagicMock, MagicMock, MagicMock],
) -> None:
    _, ydl, _ = source_io
    ydl.extract_info.return_value.pop("automatic_captions")
    with pytest.raises(ValueError, match="No English captions available"):
        ingest_source(URL)
    ydl.urlopen.assert_not_called()


def test_overview_exposes_metadata_and_derived_context(source: Source) -> None:
    overview = get_source_overview(source)
    assert overview.metadata is source.metadata
    assert overview.content_category == ContentCategory.LONG_FORM
    assert overview.chapter_count == 3
    assert overview.exclusion_lookup.status == ExclusionStatus.DISABLED
    assert overview.original_segment_count == overview.retained_segment_count == 6
    assert overview.excluded_segment_count == 0


def test_unbounded_read_returns_original_segments(source: Source) -> None:
    assert read_transcript(source) is source.transcript.segments


@pytest.mark.parametrize(
    ("start", "end", "indices"),
    [
        (120, 300, (2, 3, 4)),
        (None, 120, (0, 1)),
        (120, None, (2, 3, 4, 5)),
        (0, 120, (0, 1)),
        (120, 120, ()),
        (0, 0, ()),
        (1.25, 2, ()),
        (600, 700, ()),
        (300, 1000, (5,)),
    ],
)
def test_bounded_reads_keep_original_atoms_in_half_open_start_time_order(
    source: Source, start: float | None, end: float | None, indices: tuple[int, ...]
) -> None:
    original = source.transcript.segments
    expected = tuple(original[index] for index in indices)
    actual = read_transcript(source, start=start, end=end)
    assert actual == expected
    assert all(atom is expected_atom for atom, expected_atom in zip(actual, expected, strict=True))
    assert source.transcript.segments is original


@pytest.mark.parametrize("bound", [-1, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("name", ["start", "end"])
def test_nonfinite_or_negative_bounds_fail(source: Source, name: str, bound: float) -> None:
    with pytest.raises(ValueError, match=f"{name} must be finite and nonnegative"):
        read_transcript(source, **{name: bound})


@pytest.mark.parametrize("bound", [True, "120"])
@pytest.mark.parametrize("name", ["start", "end"])
def test_nonnumeric_bounds_fail(source: Source, name: str, bound: object) -> None:
    with pytest.raises(TypeError, match=f"{name} must be a number"):
        read_transcript(source, **{name: bound})


def test_reversed_bounds_fail(source: Source) -> None:
    with pytest.raises(ValueError, match="end must not precede start"):
        read_transcript(source, start=300, end=120)


def test_chapters_remain_unchanged_and_use_the_same_ownership_rule(source: Source) -> None:
    assert list_chapters(source) is source.metadata.chapters
    assert list_chapters(source) == (
        Chapter("First", 0, 120),
        Chapter("Second", 120, 300),
        Chapter("Empty", 400, 500),
    )
    assert read_chapter(source, 0) == read_transcript(source, start=0, end=120)
    assert read_chapter(source, 1) == read_transcript(source, start=120, end=300)
    assert read_chapter(source, 2) == ()
    assert all(
        atom is original
        for atom, original in zip(
            read_chapter(source, 1), source.transcript.segments[2:5], strict=True
        )
    )


@pytest.mark.parametrize("index", [-1, 3, 100])
def test_invalid_chapter_indices_fail(source: Source, index: int) -> None:
    with pytest.raises(IndexError, match=f"chapter_index {index} out of range for 3 chapters"):
        read_chapter(source, index)


@pytest.mark.parametrize("index", [True, 1.5, "1"])
def test_chapter_indices_must_be_integers(source: Source, index: object) -> None:
    with pytest.raises(TypeError, match="chapter_index must be an integer"):
        read_chapter(source, index)


def test_no_chapters_are_invented(source: Source) -> None:
    source = replace(source, metadata=replace(source.metadata, chapters=()))
    assert list_chapters(source) == ()
    assert get_source_overview(source).chapter_count == 0
    with pytest.raises(IndexError, match="out of range for 0 chapters"):
        read_chapter(source, 0)


def test_empty_transcript_remains_empty(source: Source) -> None:
    transcript = replace(source.transcript, segments=())
    filtered = filter_source_transcript(source.metadata, transcript, source.exclusion_lookup)
    source = replace(source, transcript=transcript, filtered_transcript=filtered)
    assert read_transcript(source) == ()
    assert read_transcript(source, start=0, end=120) == ()
    assert read_chapter(source, 0) == ()
