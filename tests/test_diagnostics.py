import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from obsidian_ingest.diagnostics import (
    GAP_THRESHOLD_SECONDS,
    MAX_REPORTED_GAPS,
    CaptionGap,
    GapPosition,
    observe_timing,
    source_diagnostics,
)
from obsidian_ingest.extractor.acquisition import Acquisition, CaptionFormat, CaptionTrack
from obsidian_ingest.extractor.metadata import ExclusionLookup, ExclusionStatus
from obsidian_ingest.extractor.providers import youtube
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata, sponsorblock
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.inspection import format_source_inspection
from obsidian_ingest.source import Source, get_source_overview, ingest_source, read_transcript

URL = "https://youtu.be/abc"


def _source(
    segments: tuple[TranscriptSegment, ...],
    duration: float | None = 600,
    acquisition: Acquisition | None = None,
) -> Source:
    info = {"id": "abc", "title": "Lesson"}
    if duration is not None:
        info["duration"] = duration
    metadata = normalize_youtube_metadata(info, URL)
    transcript = Transcript("abc", "en", "en", True, segments)
    lookup = ExclusionLookup((), ExclusionStatus.DISABLED, "SponsorBlock", ())
    filtered = filter_source_transcript(metadata, transcript, lookup)
    return Source(metadata, transcript, lookup, filtered, acquisition)


def test_contiguous_captions_report_extent_without_gaps() -> None:
    timing = observe_timing(
        *_parts(_source((TranscriptSegment("a", 0.5, 10), TranscriptSegment("b", 10, 589))))
    )
    assert timing.first_caption_start == 0.5
    assert timing.last_caption_end == 599
    assert timing.provider_duration_seconds == 600
    assert timing.gaps == ()
    assert timing.overrun_seconds is None


def _parts(source: Source):
    return source.metadata, source.transcript


def test_captions_ending_early_report_trailing_gap_and_remain_readable() -> None:
    source = _source(
        (
            TranscriptSegment("leading after silence", 45, 5),
            TranscriptSegment("overlap reaches far", 50, 40),
            TranscriptSegment("inside overlap", 60, 1),
            TranscriptSegment("after internal gap", 120, 10),
        )
    )
    timing = observe_timing(*_parts(source))
    assert timing.gaps == (
        CaptionGap(0, 45, GapPosition.LEADING),
        # Overlapping cues extend coverage; the gap starts after the furthest end.
        CaptionGap(90, 120, GapPosition.INTERNAL),
        CaptionGap(130, 600, GapPosition.TRAILING),
    )
    assert len(read_transcript(source)) == 4
    assert get_source_overview(source).timing == timing


def test_gap_below_threshold_is_not_reported() -> None:
    almost = GAP_THRESHOLD_SECONDS - 0.001
    source = _source((TranscriptSegment("a", 0, 1), TranscriptSegment("b", 1 + almost, 1)), None)
    assert observe_timing(*_parts(source)).gaps == ()


def test_unknown_duration_reports_no_trailing_gap_or_overrun() -> None:
    timing = observe_timing(*_parts(_source((TranscriptSegment("a", 0, 5000),), duration=None)))
    assert timing.provider_duration_seconds is None
    assert timing.gaps == ()
    assert timing.overrun_seconds is None
    assert timing.last_caption_end == 5000


def test_overrun_beyond_tolerance_is_reported() -> None:
    within = observe_timing(*_parts(_source((TranscriptSegment("a", 0, 101.5),), duration=100)))
    assert within.overrun_seconds is None
    beyond = observe_timing(*_parts(_source((TranscriptSegment("a", 0, 110.25),), duration=100)))
    assert beyond.overrun_seconds == 10.25


def test_empty_transcript_reports_unknown_extent() -> None:
    timing = observe_timing(*_parts(_source((), duration=60)))
    assert timing.caption_segment_count == 0
    assert timing.first_caption_start is None
    assert timing.last_caption_end is None
    assert timing.gaps == (CaptionGap(0, 60, GapPosition.TRAILING),)


def test_diagnostics_are_json_serializable_and_bounded() -> None:
    segments = tuple(TranscriptSegment(str(i), i * 100.0, 1) for i in range(MAX_REPORTED_GAPS + 5))
    acquisition = Acquisition(
        datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "yt-dlp",
        "2026.08.19",
        CaptionTrack(CaptionFormat.VTT, False, 123, "a" * 64),
    )
    data = source_diagnostics(
        _source(segments, duration=None, acquisition=acquisition), cached=True
    )
    assert json.loads(json.dumps(data, allow_nan=False)) == data
    assert data["evidence"] == {
        "kind": "captions",
        "language": "en",
        "origin": "generated",
        "format": "vtt",
        "translated": False,
        "original_payload_bytes": 123,
        "original_payload_sha256": "a" * 64,
    }
    assert data["acquisition"] == {
        "acquired_at": "2026-01-02T03:04:05+00:00",
        "extractor": "yt-dlp",
        "extractor_version": "2026.08.19",
        "reused_from_cache": True,
    }
    timing = data["timing"]
    assert timing["gap_count"] == MAX_REPORTED_GAPS + 4
    assert len(timing["gaps"]) == MAX_REPORTED_GAPS
    assert timing["gaps_truncated"] is True
    assert timing["gaps"][0] == {"start": 1.0, "end": 100.0, "position": "internal"}
    assert "not a quality score" in timing["note"]


def test_unknown_provenance_is_explicitly_none() -> None:
    data = source_diagnostics(_source((TranscriptSegment("a", 0, 1),)))
    assert data["evidence"]["format"] is None
    assert data["evidence"]["translated"] is None
    assert data["acquisition"] == {
        "acquired_at": None,
        "extractor": None,
        "extractor_version": None,
        "reused_from_cache": None,
    }
    report = format_source_inspection(_source((TranscriptSegment("a", 0, 1),)))
    assert "Captions: en, generated, format unknown, translated unknown" in report
    assert "Caption gap (trailing): 1.0-600.0 s" in report


@pytest.fixture
def vtt_only(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    factory = MagicMock()
    ydl = factory.return_value.__enter__.return_value
    ydl.extract_info.return_value = {
        "id": "abc",
        "duration": 300,
        "subtitles": {"en": [{"ext": "vtt", "url": "https://example.com/vtt"}]},
    }
    ydl.urlopen.return_value.read.return_value = (
        b"WEBVTT\n\n00:00.000 --> 00:05.000\nSpoken start\n\n"
        b"00:04.000 --> 00:10.000\nOverlapping end\n"
    )
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(
        sponsorblock, "fetch_exclusions", MagicMock(side_effect=RuntimeError("SponsorBlock down"))
    )
    return ydl


def test_vtt_source_with_early_end_and_sponsor_failure_reports_both(vtt_only) -> None:
    source = ingest_source(URL)
    assert [segment.text for segment in read_transcript(source)] == [
        "Spoken start",
        "Overlapping end",
    ]
    assert source.exclusion_lookup.status == ExclusionStatus.FAILED
    assert source.exclusion_lookup.error_message == "SponsorBlock down"
    data = source_diagnostics(source, cached=False)
    assert data["evidence"]["format"] == "vtt"
    assert data["evidence"]["origin"] == "manual"
    assert data["timing"]["gaps"] == [{"start": 10.0, "end": 300.0, "position": "trailing"}]
    assert data["acquisition"]["reused_from_cache"] is False
    assert source.original_caption.data.startswith(b"WEBVTT")
