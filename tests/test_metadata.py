from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from obsidian_ingest.extractor import ContentCategory, Provider
from obsidian_ingest.extractor.metadata import (
    Chapter,
    NativeFormat,
    SourceMetadata,
    YouTubeMetadata,
    categorize_content,
    fetch_metadata,
)
from obsidian_ingest.extractor.providers import youtube
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata

URL = "https://youtu.be/abc123"


def test_normalize_youtube_metadata() -> None:
    info = {
        "id": "abc123",
        "original_url": "https://www.youtube.com/watch?v=abc123",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "title": "A tutorial",
        "description": "Useful context",
        "channel": "Creator",
        "channel_id": "UC123",
        "channel_url": "https://www.youtube.com/channel/UC123",
        "uploader_id": "@creator",
        "duration": 900,
        "timestamp": 0,
        "upload_date": "20260925",
        "language": "en-US",
        "tags": ["python", "learning"],
        "chapters": [
            {"title": "Intro", "start_time": 0, "end_time": 60},
            {"title": "Details", "start_time": 60, "end_time": 900},
        ],
        "playlist_id": "PL123",
        "playlist_title": "Tutorials",
        "playlist_index": 2,
        "live_status": "not_live",
        "was_live": False,
    }
    metadata = normalize_youtube_metadata(info, URL)
    assert metadata == SourceMetadata(
        provider=Provider.YOUTUBE,
        source_id="abc123",
        original_url=URL,
        canonical_url=info["webpage_url"],
        title="A tutorial",
        description="Useful context",
        creator_name="Creator",
        creator_id="UC123",
        creator_url=info["channel_url"],
        duration_seconds=900.0,
        published_at=datetime(1970, 1, 1, tzinfo=UTC),
        language="en-US",
        tags=("python", "learning"),
        chapters=(Chapter("Intro", 0.0, 60.0), Chapter("Details", 60.0, 900.0)),
        native_format=NativeFormat.STANDARD,
        provider_details=YouTubeMetadata(
            channel_handle="@creator",
            playlist_id="PL123",
            playlist_title="Tutorials",
            playlist_index=2,
            is_short=False,
            live_status="not_live",
            was_live=False,
        ),
    )
    assert isinstance(metadata.duration_seconds, float)
    assert isinstance(metadata.chapters[0].start, float)
    assert isinstance(metadata.chapters[0].end, float)
    info["tags"].append("changed")
    info["chapters"][0]["title"] = "changed"
    assert metadata.tags == ("python", "learning")
    assert metadata.chapters[0].title == "Intro"
    for value, field in (
        (metadata, "title"),
        (metadata.chapters[0], "title"),
        (metadata.provider_details, "channel_handle"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, "changed")


@pytest.mark.parametrize(
    "info",
    [
        {},
        dict.fromkeys(
            [
                "id",
                "webpage_url",
                "title",
                "description",
                "channel",
                "channel_id",
                "channel_url",
                "duration",
                "timestamp",
                "upload_date",
                "language",
                "tags",
                "chapters",
                "uploader_id",
                "playlist_id",
                "playlist_title",
                "playlist_index",
                "live_status",
                "was_live",
            ]
        ),
    ],
)
def test_missing_optional_fields(info: dict) -> None:
    metadata = normalize_youtube_metadata(info, URL)
    assert metadata.source_id == "abc123"
    assert metadata.original_url == URL
    assert metadata.canonical_url == "https://www.youtube.com/watch?v=abc123"
    for field in (
        "title",
        "description",
        "creator_name",
        "creator_id",
        "creator_url",
        "duration_seconds",
        "published_at",
        "language",
    ):
        assert getattr(metadata, field) is None
    assert metadata.tags == ()
    assert metadata.chapters == ()
    assert metadata.native_format == NativeFormat.STANDARD
    assert metadata.provider_details == YouTubeMetadata(None, None, None, None, False, None, None)


def test_upload_date_fallback() -> None:
    metadata = normalize_youtube_metadata({"upload_date": "20260925"}, URL)
    assert metadata.published_at == datetime(2026, 9, 25, tzinfo=UTC)


def test_incomplete_chapters_are_omitted() -> None:
    metadata = normalize_youtube_metadata(
        {
            "chapters": [
                {"title": "Complete", "start_time": 0, "end_time": 10},
                {"title": "Missing end", "start_time": 10},
                {"title": "Unknown start", "start_time": None, "end_time": 20},
                {"start_time": 20, "end_time": 30},
            ]
        },
        URL,
    )
    assert metadata.chapters == (Chapter("Complete", 0.0, 10.0),)


@pytest.mark.parametrize("field", ["input", "original_url", "webpage_url"])
def test_short_url_evidence(field: str) -> None:
    short_url = "https://www.youtube.com/shorts/abc123"
    info = {"id": "abc123", "webpage_url": "https://www.youtube.com/watch?v=abc123"}
    url = URL
    if field == "input":
        url = short_url
    else:
        info[field] = short_url
    metadata = normalize_youtube_metadata(info, url)
    assert metadata.native_format == NativeFormat.VERTICAL_SHORT
    assert metadata.provider_details.is_short is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtu.be/abc123",
        "https://youtube.com.evil.example/shorts/abc123",
    ],
)
def test_duration_and_non_youtube_short_paths_are_not_short_evidence(url: str) -> None:
    metadata = normalize_youtube_metadata({"id": "abc123", "duration": 60}, url)
    assert metadata.native_format == NativeFormat.STANDARD
    assert metadata.provider_details.is_short is False


def test_non_handle_uploader_id_is_not_a_handle() -> None:
    metadata = normalize_youtube_metadata({"uploader_id": "UC123"}, URL)
    assert metadata.provider_details.channel_handle is None


@pytest.mark.parametrize(
    ("native_format", "duration", "expected"),
    [
        (NativeFormat.VERTICAL_SHORT, 60, ContentCategory.CLIP),
        (NativeFormat.VERTICAL_SHORT, 4000, ContentCategory.CLIP),
        (NativeFormat.VERTICAL_SHORT, None, ContentCategory.CLIP),
        (NativeFormat.STANDARD, 0, ContentCategory.SHORT_FORM),
        (NativeFormat.STANDARD, 479.9, ContentCategory.SHORT_FORM),
        (NativeFormat.STANDARD, 480, ContentCategory.LONG_FORM),
        (NativeFormat.STANDARD, 1200, ContentCategory.LONG_FORM),
        (NativeFormat.STANDARD, 3000, ContentCategory.LONG_FORM),
        (NativeFormat.STANDARD, 3000.1, ContentCategory.EXTENDED),
        (NativeFormat.STANDARD, None, None),
    ],
)
def test_categorization(
    native_format: NativeFormat, duration: float | None, expected: ContentCategory | None
) -> None:
    metadata = replace(
        normalize_youtube_metadata({}, URL),
        provider=Provider.TIKTOK,
        provider_details=None,
        native_format=native_format,
        duration_seconds=duration,
    )
    assert categorize_content(metadata) == expected


def test_fetch_metadata_normalizes_mocked_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = MagicMock()
    ydl = factory.return_value.__enter__.return_value
    ydl.extract_info.return_value = {"id": "abc123", "title": "Test", "duration": 600}
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    metadata = fetch_metadata(URL)
    assert isinstance(metadata, SourceMetadata)
    assert metadata.title == "Test"
    assert categorize_content(metadata) == ContentCategory.LONG_FORM
    ydl.extract_info.assert_called_once_with(URL, download=False)
    ydl.urlopen.assert_not_called()
    assert factory.call_args.args[0]["noplaylist"] is True


@pytest.mark.parametrize("info", [None, {"_type": "playlist"}, {"_type": "multi_video"}])
def test_fetch_metadata_rejects_non_video_results(
    info: dict | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = MagicMock()
    factory.return_value.__enter__.return_value.extract_info.return_value = info
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    with pytest.raises(ValueError, match="single YouTube video"):
        fetch_metadata(URL)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/reel/abc/",
        "https://vm.tiktok.com/abc/",
        "https://example.com/video",
    ],
)
def test_fetch_metadata_rejects_unsupported_sources(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = MagicMock()
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    with pytest.raises(ValueError):
        fetch_metadata(url)
    factory.assert_not_called()
