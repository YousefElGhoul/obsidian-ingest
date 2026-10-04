import json
import sys
from unittest.mock import MagicMock

import pytest

from obsidian_ingest import cli, segmentation
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
)
from obsidian_ingest.extractor.providers import youtube
from obsidian_ingest.extractor.providers.youtube import sponsorblock
from obsidian_ingest.extractor.transcript import TranscriptSegment


def test_default_cli_preserves_existing_transcript_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    print_transcript = MagicMock()
    inspect_source = MagicMock(side_effect=AssertionError("Inspection must be opt-in"))
    monkeypatch.setattr(sys, "argv", ["obsidian-ingest", "https://youtu.be/abc"])
    monkeypatch.setattr(cli, "print_transcript", print_transcript)
    monkeypatch.setattr(segmentation, "inspect_source", inspect_source)

    cli.main()

    print_transcript.assert_called_once_with("https://youtu.be/abc")
    inspect_source.assert_not_called()
    assert capsys.readouterr().out == ""


def test_inspect_cli_formats_inspected_source_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = object()
    inspect_source = MagicMock(return_value=result)
    format_segmentation = MagicMock(return_value="Baseline chunks\nSource indices: 0, 2")
    print_transcript = MagicMock(side_effect=AssertionError("Must not print raw sponsor text"))
    monkeypatch.setattr(
        sys, "argv", ["obsidian-ingest", "--inspect-chunks", "https://youtu.be/abc"]
    )
    monkeypatch.setattr(segmentation, "inspect_source", inspect_source)
    monkeypatch.setattr(segmentation, "format_segmentation", format_segmentation)
    monkeypatch.setattr(cli, "print_transcript", print_transcript)

    cli.main()

    inspect_source.assert_called_once_with("https://youtu.be/abc")
    format_segmentation.assert_called_once_with(result)
    print_transcript.assert_not_called()
    assert capsys.readouterr().out == "Baseline chunks\nSource indices: 0, 2\n"


@pytest.fixture
def source_io(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[MagicMock, dict, ExclusionLookup, MagicMock]:
    info = {
        "id": "snapshot-id",
        "title": "One snapshot",
        "duration": 600,
        "chapters": [
            {"title": "Intro", "start_time": 0, "end_time": 2},
            {"title": "Lesson", "start_time": 2, "end_time": 600},
        ],
        "automatic_captions": {"en": [{"ext": "json3", "url": "https://example.com/captions"}]},
    }
    exclusions = ExclusionLookup(
        (ExcludedRange(1, 2, "sponsor", "SponsorBlock"),),
        ExclusionStatus.COMPLETE,
        "SponsorBlock",
        ("sponsor",),
    )
    factory = MagicMock()
    ydl = factory.return_value.__enter__.return_value
    ydl.extract_info.return_value = info
    ydl.urlopen.return_value.read.return_value = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 125,
                    "dDurationMs": 2000,
                    "segs": [{"utf8": " Hello "}, {"utf8": "world"}],
                },
                {"tStartMs": 1000, "dDurationMs": 1500, "segs": [{"utf8": "SECRET SPONSOR"}]},
                {"tStartMs": 2125, "dDurationMs": 750, "segs": [{"utf8": "Lesson"}]},
            ]
        }
    ).encode()
    fetch_exclusions = MagicMock(return_value=exclusions)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(sponsorblock, "fetch_exclusions", fetch_exclusions)
    return ydl, info, exclusions, fetch_exclusions


def test_combined_youtube_source_uses_one_snapshot_and_real_normalization(
    source_io: tuple[MagicMock, dict, ExclusionLookup, MagicMock],
) -> None:
    ydl, info, exclusions, fetch_exclusions = source_io
    metadata, transcript, actual_lookup = youtube.get_youtube_source("https://youtu.be/request-id")

    ydl.extract_info.assert_called_once_with("https://youtu.be/request-id", download=False)
    fetch_exclusions.assert_called_once_with(ydl, info)
    assert fetch_exclusions.call_args.args[1] is info
    ydl.urlopen.assert_called_once_with("https://example.com/captions")
    assert metadata.source_id == transcript.video_id == "snapshot-id"
    assert metadata.original_url == "https://youtu.be/request-id"
    assert metadata.title == "One snapshot"
    assert metadata.duration_seconds == 600
    assert metadata.chapters == (Chapter("Intro", 0, 2), Chapter("Lesson", 2, 600))
    assert (transcript.language, transcript.language_code, transcript.is_generated) == (
        "en",
        "en",
        True,
    )
    assert transcript.segments == (
        TranscriptSegment("Hello world", 0.125, 2),
        TranscriptSegment("SECRET SPONSOR", 1, 1.5),
        TranscriptSegment("Lesson", 2.125, 0.75),
    )
    assert actual_lookup is exclusions


def test_inspect_cli_offline_integration_filters_without_mutating_raw_captions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source_io: tuple[MagicMock, dict, ExclusionLookup, MagicMock],
) -> None:
    ydl, _, _, fetch_exclusions = source_io
    monkeypatch.setattr(
        sys, "argv", ["obsidian-ingest", "https://youtu.be/abc", "--inspect-chunks"]
    )

    cli.main()

    ydl.extract_info.assert_called_once_with("https://youtu.be/abc", download=False)
    fetch_exclusions.assert_called_once()
    report = capsys.readouterr().out
    assert "Segments: 3 original, 2 retained, 1 excluded" in report
    assert "SECRET SPONSOR" not in report
    assert "sponsor (SponsorBlock); excluded indices: 1" in report
    assert "Segment 0: retained, crosses range 0" in report
    assert "Segment 1: excluded, crosses range 0" in report
    assert "[0] [00:00.125] Hello world" in report
    assert "[2] [00:02.125] Lesson" in report
    assert "Chapter: 0: Intro" in report
    assert "Chapter: 1: Lesson" in report


def test_lookup_failure_prints_no_chunks_and_does_not_fetch_captions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    source_io: tuple[MagicMock, dict, ExclusionLookup, MagicMock],
) -> None:
    ydl, _, _, fetch_exclusions = source_io
    fetch_exclusions.side_effect = ValueError("SponsorBlock lookup failed")
    monkeypatch.setattr(
        sys, "argv", ["obsidian-ingest", "--inspect-chunks", "https://youtu.be/abc"]
    )

    with pytest.raises(ValueError, match="SponsorBlock lookup failed"):
        cli.main()

    ydl.extract_info.assert_called_once_with("https://youtu.be/abc", download=False)
    fetch_exclusions.assert_called_once()
    ydl.urlopen.assert_not_called()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("info", [None, {}, {"_type": "playlist"}, {"_type": "multi_video"}])
def test_combined_source_rejects_non_video_before_lookup_or_captions(
    source_io: tuple[MagicMock, dict, ExclusionLookup, MagicMock], info: dict | None
) -> None:
    ydl, _, _, fetch_exclusions = source_io
    ydl.extract_info.return_value = info
    with pytest.raises(ValueError, match="Expected metadata for a single YouTube video"):
        youtube.get_youtube_source("https://youtu.be/abc")
    ydl.extract_info.assert_called_once_with("https://youtu.be/abc", download=False)
    fetch_exclusions.assert_not_called()
    ydl.urlopen.assert_not_called()


def test_inspection_rejects_unsupported_provider_before_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_source = MagicMock(side_effect=AssertionError("No extraction expected"))
    monkeypatch.setattr(youtube, "get_youtube_source", get_source)
    with pytest.raises(ValueError, match="currently supported only for YouTube"):
        segmentation.inspect_source("https://www.instagram.com/reel/abc/")
    get_source.assert_not_called()
