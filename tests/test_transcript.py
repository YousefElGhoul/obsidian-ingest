import json
from unittest.mock import MagicMock

import pytest

from obsidian_ingest.extractor import Provider, detect_provider
from obsidian_ingest.extractor.acquisition import (
    MAX_CAPTION_BYTES,
    CaptionError,
    CaptionFormat,
    OriginalCaption,
)
from obsidian_ingest.extractor.providers import youtube
from obsidian_ingest.extractor.providers.youtube import (
    choose_caption_format,
    choose_caption_track,
    extract_video_id,
    get_youtube_content,
    parse_captions,
    parse_json3,
    parse_vtt,
    sponsorblock,
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


@pytest.mark.parametrize("manual_language", ["en", "en-US"])
def test_manual_english_still_precedes_original_automatic_captions(manual_language: str) -> None:
    manual = [{"ext": "json3", "url": "https://example.com/manual"}]
    info = {
        "subtitles": {manual_language: manual},
        "automatic_captions": {
            "en-orig": [{"ext": "json3", "url": "https://example.com/original"}]
        },
    }
    assert choose_caption_track(info) == (manual_language, False, manual)


@pytest.mark.parametrize("original_language", ["en-orig", "en-US-orig", "en-GB-orig"])
def test_original_english_asr_precedes_bare_english_and_normalizes_language(
    original_language: str,
) -> None:
    original = [{"ext": "json3", "url": "https://example.com/original"}]
    info = {
        "automatic_captions": {
            "en": [{"ext": "json3", "url": "https://example.com/translated"}],
            original_language: original,
        }
    }
    assert choose_caption_track(info) == (original_language.removesuffix("-orig"), True, original)


def test_exact_original_english_precedes_original_variants() -> None:
    original = [{"ext": "json3", "url": "https://example.com/original"}]
    info = {
        "automatic_captions": {
            "en-US-orig": [{"ext": "json3", "url": "https://example.com/variant"}],
            "en-orig": original,
        }
    }
    assert choose_caption_track(info) == ("en", True, original)


def test_original_english_variants_are_selected_in_stable_order() -> None:
    british = [{"ext": "json3", "url": "https://example.com/british"}]
    info = {
        "automatic_captions": {
            "en-US-orig": [{"ext": "json3", "url": "https://example.com/american"}],
            "en-GB-orig": british,
        }
    }
    assert choose_caption_track(info) == ("en-GB", True, british)


def test_original_english_only_is_selected_without_bare_english() -> None:
    original = [{"ext": "json3", "url": "https://example.com/original"}]
    assert choose_caption_track({"automatic_captions": {"en-orig": original}}) == (
        "en",
        True,
        original,
    )


def test_non_english_original_does_not_override_english_fallback() -> None:
    english = [{"ext": "json3", "url": "https://example.com/english"}]
    assert choose_caption_track(
        {
            "automatic_captions": {
                "ar-orig": [{"ext": "json3", "url": "https://example.com/arabic"}],
                "en": english,
            }
        }
    ) == ("en", True, english)


def test_automatic_english_variant_fallback_without_original() -> None:
    english = [{"ext": "json3", "url": "https://example.com/english"}]
    assert choose_caption_track({"automatic_captions": {"en-US": english}}) == (
        "en-US",
        True,
        english,
    )


def test_no_english_original_or_fallback_raises() -> None:
    with pytest.raises(ValueError, match="No English captions available"):
        choose_caption_track({"automatic_captions": {"ar-orig": [{"ext": "json3"}]}})


def test_choose_caption_format_prefers_json3_then_vtt() -> None:
    formats = [{"ext": "vtt"}, {"ext": "json3"}]
    assert choose_caption_format(formats) == {"ext": "json3"}
    assert choose_caption_format([{"ext": "vtt"}]) == {"ext": "vtt"}


def test_unsupported_caption_formats_fail_explicitly() -> None:
    with pytest.raises(CaptionError, match="offered: srv1, ttml"):
        choose_caption_format([{"ext": "ttml"}, {"ext": "srv1"}])
    with pytest.raises(CaptionError, match="offered: none"):
        choose_caption_format([])


def test_parse_vtt_keeps_cues_multiline_overlapping_and_unicode() -> None:
    text = (
        "﻿WEBVTT Kind: captions\nLanguage: en\n\n"
        "STYLE\n::cue { color: lime }\n\n"
        "NOTE a comment\nthat spans lines\n\n"
        "intro\n00:00:01.250 --> 00:00:04.000 align:start position:0%\n"
        "<v Speaker>Café &amp; naïve</v>\n<c.loud>second</c> line  \n\n"
        "00:02.000 --> 00:03.500\nOverlapping 日本語 🙂\n\n"
        "00:00:05.000 --> 00:00:06.000\n\n"
        "1:00:00.000 --> 1:00:00.010\n<00:00:01.000><c> rolling</c>\n\n"
    )
    assert parse_vtt(text) == (
        TranscriptSegment("Café & naïve\nsecond line", 1.25, 2.75),
        TranscriptSegment("Overlapping 日本語 🙂", 2.0, 1.5),
        TranscriptSegment("rolling", 3600.0, 0.01),
    )


def test_parse_vtt_accepts_crlf() -> None:
    assert parse_vtt("WEBVTT\r\n\r\n00:01.000 --> 00:02.000\r\nHi\r\n") == (
        TranscriptSegment("Hi", 1.0, 1.0),
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("00:01.000 --> 00:02.000\nNo header", "missing WEBVTT header"),
        ("WEBVTTX\n\n00:01.000 --> 00:02.000\nHi", "missing WEBVTT header"),
        ("WEBVTT\n\n00:01 --> 00:02\nBad timing", "Malformed WebVTT cue timing"),
        ("WEBVTT\n\nidentifier only", "Malformed WebVTT cue timing"),
        ("WEBVTT\n\n00:01:61.000 --> 00:02.000\nHi", "Malformed WebVTT cue timing"),
        ("WEBVTT\n\n00:03.000 --> 00:02.000\nBackwards", "ends before it starts"),
        (
            "WEBVTT\n\n00:03.000 --> 00:04.000\nB\n\n00:01.000 --> 00:02.000\nA",
            "not in start-time order",
        ),
        # A cue without a blank line after the header must not be dropped silently.
        (
            "WEBVTT\n00:00.000 --> 00:01.000\nFIRST\n\n00:40.000 --> 00:41.000\nSECOND\n",
            "cue timing inside the header block",
        ),
        (
            "WEBVTT Kind: captions\nLanguage: en\n00:00.000 --> 00:01.000\nFIRST",
            "cue timing inside the header block",
        ),
    ],
)
def test_malformed_vtt_fails_explicitly(text: str, message: str) -> None:
    with pytest.raises(CaptionError, match=message):
        parse_vtt(text)


@pytest.mark.parametrize(
    ("data", "caption_format", "message"),
    [
        (b"", CaptionFormat.JSON3, "Empty json3"),
        (b"  \n", CaptionFormat.VTT, "Empty vtt"),
        (b"\xff\xfe", CaptionFormat.JSON3, "not UTF-8"),
        (b"{not json", CaptionFormat.JSON3, "Malformed JSON3"),
        (b"[]", CaptionFormat.JSON3, "must be an object"),
        (b'{"events": {}}', CaptionFormat.JSON3, "events must be a list"),
        (b'{"events": [1]}', CaptionFormat.JSON3, "event 0 must be an object"),
        (
            b'{"events": [{"tStartMs": "1", "dDurationMs": 1, "segs": [{"utf8": "x"}]}]}',
            CaptionFormat.JSON3,
            "non-numeric tStartMs",
        ),
        (
            b'{"events": [{"tStartMs": -1, "dDurationMs": 1, "segs": [{"utf8": "x"}]}]}',
            CaptionFormat.JSON3,
            "invalid tStartMs",
        ),
        (
            b'{"events": [{"tStartMs": 1, "dDurationMs": 1, "segs": [{"utf8": 5}]}]}',
            CaptionFormat.JSON3,
            "non-text",
        ),
        (b'{"events": []}', CaptionFormat.JSON3, "no timed caption text"),
        (b'{"wireMagic": "pb3"}', CaptionFormat.JSON3, "no timed caption text"),
        (b"WEBVTT\n\nNOTE only\n", CaptionFormat.VTT, "no timed caption text"),
    ],
)
def test_unusable_caption_responses_fail_explicitly(
    data: bytes, caption_format: CaptionFormat, message: str
) -> None:
    with pytest.raises(CaptionError, match=message):
        parse_captions(data, caption_format)


def test_parse_captions_dispatches_by_format() -> None:
    json3 = json.dumps(
        {"events": [{"tStartMs": 0, "dDurationMs": 5, "segs": [{"utf8": "Ünïcode"}]}]},
        ensure_ascii=False,
    ).encode()
    assert parse_captions(json3, CaptionFormat.JSON3) == (TranscriptSegment("Ünïcode", 0, 0.005),)
    vtt = b"WEBVTT\n\n00:00.000 --> 00:00.005\n\xc3\x9cn\xc3\xafcode\n"
    assert parse_captions(vtt, CaptionFormat.VTT) == (TranscriptSegment("Ünïcode", 0, 0.005),)


def _content(monkeypatch: pytest.MonkeyPatch, info: dict, payload: object) -> MagicMock:
    factory = MagicMock()
    ydl = factory.return_value.__enter__.return_value
    ydl.extract_info.return_value = {"id": "abc123", "duration": 10, **info}
    ydl.urlopen.return_value.read.return_value = payload
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(
        sponsorblock, "fetch_exclusions", MagicMock(side_effect=RuntimeError("offline"))
    )
    return ydl


JSON3 = b'{"events": [{"tStartMs": 0, "dDurationMs": 1000, "segs": [{"utf8": "Hi"}]}]}'


@pytest.mark.parametrize(
    ("info", "language", "generated", "translated"),
    [
        ({"subtitles": {"en": [{"ext": "json3", "url": "https://x/m"}]}}, "en", False, None),
        (
            {"automatic_captions": {"en-orig": [{"ext": "json3", "url": "https://x/o"}]}},
            "en",
            True,
            False,
        ),
        (
            {"automatic_captions": {"en": [{"ext": "json3", "url": "https://x/t?tlang=en"}]}},
            "en",
            True,
            True,
        ),
        (
            {"automatic_captions": {"en-US": [{"ext": "json3", "url": "https://x/a"}]}},
            "en-US",
            True,
            None,
        ),
    ],
)
def test_content_records_caption_provenance_without_guessing(
    monkeypatch: pytest.MonkeyPatch,
    info: dict,
    language: str,
    generated: bool,
    translated: bool | None,
) -> None:
    _content(monkeypatch, info, JSON3)
    content = get_youtube_content("https://youtu.be/abc123")
    assert content.transcript.language == language
    assert content.transcript.is_generated is generated
    track = content.acquisition.caption
    assert track.format == CaptionFormat.JSON3
    assert track.is_translated is translated
    assert track.payload_bytes == len(JSON3)
    assert content.original_caption == OriginalCaption(CaptionFormat.JSON3, JSON3)
    assert track.payload_sha256 == content.original_caption.sha256
    assert content.acquisition.extractor == "yt-dlp"
    assert content.acquisition.extractor_version == youtube.yt_dlp.version.__version__
    assert content.acquisition.acquired_at.tzinfo is not None
    assert content.exclusions.error_message == "offline"
    # Signed caption URLs are used for retrieval only and never recorded.
    assert "https://x" not in repr(content.acquisition)


def test_unknown_extractor_version_stays_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _content(monkeypatch, {"subtitles": {"en": [{"ext": "json3", "url": "u"}]}}, JSON3)
    monkeypatch.delattr(youtube.yt_dlp.version, "__version__")
    assert get_youtube_content("https://youtu.be/abc123").acquisition.extractor_version is None


def test_oversized_or_non_bytes_caption_response_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    ydl = _content(monkeypatch, {"subtitles": {"en": [{"ext": "vtt", "url": "u"}]}}, b"")
    ydl.urlopen.return_value.read.return_value = b"x" * (MAX_CAPTION_BYTES + 1)
    with pytest.raises(CaptionError, match="exceeds"):
        get_youtube_content("https://youtu.be/abc123")
    ydl.urlopen.return_value.read.assert_called_with(MAX_CAPTION_BYTES + 1)
    ydl.urlopen.return_value.read.return_value = "text"
    with pytest.raises(CaptionError, match="did not return bytes"):
        get_youtube_content("https://youtu.be/abc123")


def test_selected_format_without_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _content(monkeypatch, {"subtitles": {"en": [{"ext": "json3"}]}}, JSON3)
    with pytest.raises(CaptionError, match="no URL"):
        get_youtube_content("https://youtu.be/abc123")


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
