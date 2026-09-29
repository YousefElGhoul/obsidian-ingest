import json
from unittest.mock import MagicMock

import pytest

from obsidian_ingest.extractor import Provider, detect_provider
from obsidian_ingest.extractor.providers import youtube
from obsidian_ingest.extractor.providers.youtube import (
    choose_caption_format,
    choose_caption_track,
    extract_video_id,
    parse_json3,
)
from obsidian_ingest.extractor.transcript import (
    TranscriptSegment,
    print_transcript,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc123", "abc123"),
        ("https://youtu.be/abc123", "abc123"),
        ("https://www.youtube.com/shorts/abc123", "abc123"),
        ("https://www.youtube.com/live/abc123", "abc123"),
    ],
)
def test_extract_video_id(url: str, expected: str) -> None:
    assert extract_video_id(url) == expected


def test_extract_video_id_rejects_non_youtube_url() -> None:
    with pytest.raises(ValueError):
        extract_video_id("https://example.com/video")


def test_parse_json3_keeps_useful_events_atomic() -> None:
    captions = {
        "events": [
            {
                "tStartMs": 1250,
                "dDurationMs": 900,
                "segs": [{"utf8": " Hello "}, {"utf8": "world"}],
            },
            {"tStartMs": 2150, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 2250, "dDurationMs": 100, "segs": [{"utf8": "next", "tOffsetMs": 50}]},
            {"segs": [{"utf8": "missing time"}]},
            {"tStartMs": 3000, "dDurationMs": 100},
        ]
    }

    assert parse_json3(captions) == (
        TranscriptSegment(text="Hello world", start=1.25, duration=0.9),
        TranscriptSegment(text="next", start=2.25, duration=0.1),
    )


def test_choose_caption_track_prefers_manual_english() -> None:
    info = {
        "subtitles": {"en-US": [{"ext": "vtt"}]},
        "automatic_captions": {"en": [{"ext": "json3"}]},
    }

    assert choose_caption_track(info) == ("en-US", False, [{"ext": "vtt"}])


def test_choose_caption_track_falls_back_to_automatic_english() -> None:
    info = {"automatic_captions": {"en": [{"ext": "json3"}]}}

    assert choose_caption_track(info) == ("en", True, [{"ext": "json3"}])


def test_choose_caption_format_prefers_json3_then_vtt() -> None:
    formats = [{"ext": "vtt"}, {"ext": "json3"}]
    assert choose_caption_format(formats) == {"ext": "json3"}
    assert choose_caption_format([{"ext": "vtt"}]) == {"ext": "vtt"}


def test_detect_youtube() -> None:
    assert detect_provider("https://www.youtube.com/watch?v=abc") == Provider.YOUTUBE
    assert detect_provider("https://youtu.be/abc") == Provider.YOUTUBE


def test_detect_instagram() -> None:
    assert detect_provider("https://www.instagram.com/reel/abc/") == Provider.INSTAGRAM


def test_detect_tiktok() -> None:
    assert detect_provider("https://www.tiktok.com/@user/video/123") == Provider.TIKTOK


def test_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        detect_provider("https://example.com/video")


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com.evil.example/watch?v=abc",
        "https://notyoutube.com/watch?v=abc",
        "https://example.com/youtube.com/watch?v=abc",
        "https://youtube.com@evil.example/watch?v=abc",
    ],
)
def test_rejects_spoofed_provider(url: str) -> None:
    with pytest.raises(ValueError):
        detect_provider(url)


def test_choose_caption_track_prefers_exact_english() -> None:
    formats = [{"ext": "json3"}]
    assert choose_caption_track({"subtitles": {"en-US": [{"ext": "vtt"}], "en": formats}}) == (
        "en",
        False,
        formats,
    )


def test_print_transcript_with_mocked_extraction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = "https://www.youtube.com/watch?v=abc123"
    factory = MagicMock()
    ydl = factory.return_value.__enter__.return_value
    ydl.extract_info.return_value = {
        "automatic_captions": {"en": [{"ext": "json3", "url": "https://example.com/captions"}]}
    }
    ydl.urlopen.return_value.read.return_value = json.dumps(
        {
            "events": [
                {"tStartMs": 1250, "dDurationMs": 900, "segs": [{"utf8": " Hello world "}]},
                {"tStartMs": 3600000, "dDurationMs": 1000, "segs": [{"utf8": "Next"}]},
            ]
        }
    ).encode()
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)

    print_transcript(url)

    ydl.extract_info.assert_called_once_with(url, download=False)
    ydl.urlopen.assert_called_once_with("https://example.com/captions")
    assert capsys.readouterr().out == (
        "Video: abc123\nLanguage: en (en)\nGenerated captions: True\nSegments: 2\n\n"
        "[00:01] Hello world\n[01:00:00] Next\n"
    )
