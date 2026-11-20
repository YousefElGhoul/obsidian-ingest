import json
from unittest.mock import MagicMock

import pytest

from obsidian_ingest.cli import print_transcript
from obsidian_ingest.extractor import Provider
from obsidian_ingest.extractor.acquisition import (
    MAX_CAPTION_BYTES,
    CaptionError,
    CaptionFormat,
    OriginalCaption,
)
from obsidian_ingest.extractor.providers import resolve_url, youtube
from obsidian_ingest.extractor.providers.youtube import (
    choose_caption_format,
    choose_caption_track,
    extract_video_id,
    get_youtube_content,
    sponsorblock,
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


def _track(name: str) -> list[dict[str, str]]:
    return [{"ext": "json3", "url": f"https://example.com/{name}"}]


@pytest.mark.parametrize(
    ("subtitles", "automatic", "expected"),
    [
        ({"en-US": _track("manual")}, {"en": _track("auto")}, ("en-US", False, "manual")),
        ({"en-US": _track("variant"), "en": _track("exact")}, {}, ("en", False, "exact")),
        ({}, {"en": _track("auto")}, ("en", True, "auto")),
        ({"en": _track("manual")}, {"en-orig": _track("original")}, ("en", False, "manual")),
        ({"en-US": _track("manual")}, {"en-orig": _track("original")}, ("en-US", False, "manual")),
        ({}, {"en": _track("translated"), "en-orig": _track("original")}, ("en", True, "original")),
        (
            {},
            {"en": _track("translated"), "en-US-orig": _track("original")},
            ("en-US", True, "original"),
        ),
        (
            {},
            {"en": _track("translated"), "en-GB-orig": _track("original")},
            ("en-GB", True, "original"),
        ),
        (
            {},
            {"en-US-orig": _track("variant"), "en-orig": _track("original")},
            ("en", True, "original"),
        ),
        (
            {},
            {"en-US-orig": _track("american"), "en-GB-orig": _track("british")},
            ("en-GB", True, "british"),
        ),
        ({}, {"en-orig": _track("original")}, ("en", True, "original")),
        ({}, {"ar-orig": _track("arabic"), "en": _track("english")}, ("en", True, "english")),
        ({}, {"en-US": _track("english")}, ("en-US", True, "english")),
    ],
    ids=[
        "manual-english-before-automatic",
        "exact-manual-english-before-variant",
        "automatic-english-fallback",
        "manual-en-before-original-asr",
        "manual-en-US-before-original-asr",
        "original-asr-before-translated-en",
        "original-en-US-asr-normalizes-language",
        "original-en-GB-asr-normalizes-language",
        "exact-original-before-original-variants",
        "original-variants-in-stable-order",
        "original-only",
        "non-english-original-does-not-override-english",
        "automatic-english-variant-without-original",
    ],
)
def test_caption_track_preference_order(
    subtitles: dict, automatic: dict, expected: tuple[str, bool, str]
) -> None:
    language, generated, name = expected
    info = {"subtitles": subtitles, "automatic_captions": automatic}
    assert choose_caption_track(info) == (language, generated, _track(name))


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


@pytest.mark.parametrize(
    ("url", "provider"),
    [
        ("https://www.youtube.com/watch?v=abc", Provider.YOUTUBE),
        ("https://youtu.be/abc", Provider.YOUTUBE),
        ("https://www.instagram.com/reel/abc/", Provider.INSTAGRAM),
        ("https://www.tiktok.com/@user/video/123", Provider.TIKTOK),
    ],
)
def test_resolve_url_by_host(url: str, provider: Provider) -> None:
    assert resolve_url(url).name == provider


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/video",
        "https://youtube.com.evil.example/watch?v=abc",
        "https://notyoutube.com/watch?v=abc",
        "https://example.com/youtube.com/watch?v=abc",
        "https://youtube.com@evil.example/watch?v=abc",
    ],
    ids=["unknown", "suffix-spoof", "prefix-spoof", "path-spoof", "userinfo-spoof"],
)
def test_unknown_or_spoofed_provider_is_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        resolve_url(url)


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
        "Source: abc123\nLanguage: en (en)\nGenerated captions: True\nSegments: 2\n\n"
        "[00:01] Hello world\n[01:00:00] Next\n"
    )
