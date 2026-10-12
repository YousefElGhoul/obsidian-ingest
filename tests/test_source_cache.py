import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier
from unittest.mock import MagicMock

import pytest

from obsidian_ingest import source_cache
from obsidian_ingest.extractor.metadata import ExcludedRange, ExclusionLookup, ExclusionStatus
from obsidian_ingest.extractor.providers import youtube
from obsidian_ingest.extractor.providers.youtube import sponsorblock
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.source import Source, ingest_source
from obsidian_ingest.source_cache import SourceCache, SourceCacheError, default_cache_path

URL = "https://www.youtube.com/watch?v=video-123"


@pytest.fixture
def extraction(monkeypatch):
    factory = MagicMock()
    ydl = factory.return_value.__enter__.return_value
    ydl.extract_info.return_value = {
        "id": "video-123",
        "webpage_url": URL,
        "title": "Original title",
        "description": "Original\ncontext",
        "channel": "Creator",
        "channel_id": "channel-1",
        "channel_url": "https://www.youtube.com/channel/channel-1",
        "uploader_id": "@creator",
        "duration": 600.125,
        "timestamp": 1234567890,
        "language": "en-US",
        "tags": ["one", "two"],
        "media_type": "video",
        "playlist_id": "playlist-1",
        "playlist_title": "Playlist",
        "playlist_index": 2,
        "live_status": "was_live",
        "was_live": True,
        "chapters": [{"title": "Main", "start_time": 0, "end_time": 600.125}],
        "subtitles": {"en-US": [{"ext": "json3", "url": "https://example.com/captions"}]},
    }
    ydl.urlopen.return_value.read.return_value = json.dumps(
        {
            "events": [
                {"tStartMs": 125, "dDurationMs": 2000, "segs": [{"utf8": "Original\nwords"}]},
                {"tStartMs": 1000, "dDurationMs": 500, "segs": [{"utf8": "Sponsor"}]},
                {"tStartMs": 1000, "dDurationMs": 100, "segs": [{"utf8": "Same start"}]},
                {"tStartMs": 2125, "dDurationMs": 750, "segs": [{"utf8": "Next"}]},
            ]
        }
    ).encode()

    def exclusions(ydl, info, categories):
        return ExclusionLookup(
            (ExcludedRange(1, 2, "sponsor", "SponsorBlock"),) if categories else (),
            ExclusionStatus.COMPLETE if categories else ExclusionStatus.DISABLED,
            "SponsorBlock",
            categories,
            datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=UTC),
        )

    lookup = MagicMock(side_effect=exclusions)
    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", factory)
    monkeypatch.setattr(sponsorblock, "fetch_exclusions", lookup)
    return factory, ydl, lookup


def test_miss_hit_equivalent_urls_and_complete_round_trip(tmp_path, extraction):
    factory, ydl, lookup = extraction
    database = tmp_path / "sources.db"
    cache = SourceCache(database)
    first = cache.ingest(URL)
    assert first.cached is False
    assert first.source_id != "video-123"
    assert first.source.filtered_transcript.retained_indices == (0, 3)
    assert first.source.transcript.segments[0].start == 0.125
    assert first.source.transcript.segments[0].duration == 2
    assert first.source.metadata.provider_details.playlist_index == 2
    ydl.extract_info.assert_called_once_with(URL, download=False)
    factory.reset_mock()
    lookup.reset_mock()

    restarted = SourceCache(database)
    for url in (
        URL,
        "https://youtu.be/video-123?t=12",
        "https://m.youtube.com/watch?v=video-123&list=abc",
        "https://www.youtube.com/shorts/video-123",
        "https://youtube.com/embed/video-123",
        "https://youtube.com/live/video-123",
    ):
        hit = restarted.ingest(url)
        assert hit.cached is True
        assert hit.source_id == first.source_id
        assert hit.source == first.source
        assert hit.source.metadata.original_url == URL
        assert restarted.get(hit.source_id) == first.source
        assert hit.source.filtered_transcript.original is hit.source.transcript
    factory.assert_not_called()
    ydl.extract_info.assert_not_called()
    ydl.urlopen.assert_not_called()
    lookup.assert_not_called()
    with closing(sqlite3.connect(database)) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM sources").fetchone()[0])
    assert payload["extraction"]["extractor"] == "yt-dlp"
    assert payload["extraction"]["extractor_version"]
    assert datetime.fromisoformat(payload["extraction"]["extracted_at"]).tzinfo is not None
    assert set(payload) == {"version", "extraction", "metadata", "transcript", "exclusion_lookup"}


@pytest.mark.parametrize("first_option", [True, False])
def test_filter_options_have_independent_persistent_handles(tmp_path, extraction, first_option):
    _, ydl, lookup = extraction
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL, exclude_sponsors=first_option)
    second = cache.ingest(URL, exclude_sponsors=not first_option)
    assert second.cached is False
    assert second.source_id != first.source_id
    assert ydl.extract_info.call_count == 2
    assert [call.args[2] for call in lookup.call_args_list] == [
        ("sponsor",) if first_option else (),
        () if first_option else ("sponsor",),
    ]
    ydl.reset_mock()
    for result, option in ((first, first_option), (second, not first_option)):
        hit = cache.ingest(URL, exclude_sponsors=option)
        assert hit.source_id == result.source_id
        assert hit.cached
        assert hit.source.exclusion_lookup.categories == (("sponsor",) if option else ())
        assert len(hit.source.filtered_transcript.retained_indices) == (2 if option else 4)
    ydl.extract_info.assert_not_called()


def test_failed_extraction_is_not_cached_and_failed_refresh_preserves_data(tmp_path, extraction):
    _, ydl, _ = extraction
    cache = SourceCache(tmp_path / "sources.db")
    ydl.extract_info.side_effect = RuntimeError("Unavailable")
    with pytest.raises(RuntimeError, match="Unavailable"):
        cache.ingest(URL)
    with closing(sqlite3.connect(cache.path)) as connection:
        assert connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
    ydl.extract_info.side_effect = None
    first = cache.ingest(URL)
    ydl.extract_info.side_effect = RuntimeError("Refresh unavailable")
    with pytest.raises(RuntimeError, match="Refresh unavailable"):
        cache.ingest(URL, refresh=True)
    assert cache.get(first.source_id) == first.source
    assert cache.ingest(URL).cached


def test_refresh_updates_snapshot_without_changing_id(tmp_path, extraction):
    _, ydl, _ = extraction
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    ydl.extract_info.return_value["title"] = "Updated title"
    refreshed = cache.ingest(URL, refresh=True)
    assert refreshed.source_id == first.source_id
    assert not refreshed.cached
    assert refreshed.source.metadata.title == "Updated title"
    assert cache.get(first.source_id) == refreshed.source


def test_failed_sponsor_lookup_round_trips_and_requires_explicit_refresh(tmp_path, extraction):
    _, ydl, lookup = extraction
    lookup.side_effect = RuntimeError("SponsorBlock unavailable")
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    assert first.source.exclusion_lookup.status == ExclusionStatus.FAILED
    assert first.source.exclusion_lookup.error_message == "SponsorBlock unavailable"
    assert first.source.filtered_transcript.retained_indices == (0, 1, 2, 3)
    ydl.reset_mock()
    assert SourceCache(cache.path).ingest(URL).source == first.source
    ydl.extract_info.assert_not_called()


@pytest.mark.parametrize(
    "corruption",
    ["checksum", "json", "missing", "version", "type", "category", "timing", "identity"],
)
def test_corrupt_entries_fail_explicitly_and_refresh_recovers(tmp_path, extraction, corruption):
    _, ydl, _ = extraction
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    with closing(sqlite3.connect(cache.path)) as connection, connection:
        payload = connection.execute("SELECT payload FROM sources").fetchone()[0]
        data = json.loads(payload)
        if corruption == "missing":
            del data["transcript"]["segments"]
        elif corruption == "version":
            data["version"] = 99
        elif corruption == "type":
            data["transcript"]["segments"][0]["text"] = 123
        elif corruption == "category":
            data["exclusion_lookup"]["ranges"][0]["reason"] = "intro"
        elif corruption == "timing":
            data["transcript"]["segments"][0]["start"] = -1
        elif corruption == "identity":
            data["transcript"]["video_id"] = "another-video"
        payload = "not JSON" if corruption == "json" else json.dumps(data)
        checksum = (
            "broken" if corruption == "checksum" else hashlib.sha256(payload.encode()).hexdigest()
        )
        connection.execute("UPDATE sources SET payload = ?, checksum = ?", (payload, checksum))
    ydl.reset_mock()
    with pytest.raises(SourceCacheError, match="Invalid source cache entry.*refresh=True"):
        cache.ingest(URL)
    with pytest.raises(SourceCacheError, match="Invalid source cache entry"):
        cache.get(first.source_id)
    ydl.extract_info.assert_not_called()
    recovered = cache.ingest(URL, refresh=True)
    assert recovered.source_id == first.source_id
    assert recovered.source == first.source


def test_missing_source_id_is_explicit(tmp_path):
    with pytest.raises(KeyError):
        SourceCache(tmp_path / "sources.db").get("missing")


def test_optional_fields_empty_collections_and_generated_flag_round_trip(
    tmp_path, extraction, monkeypatch
):
    snapshot = ingest_source(URL, exclude_sponsors=False)
    metadata = replace(
        snapshot.metadata,
        title=None,
        description=None,
        creator_name=None,
        creator_id=None,
        creator_url=None,
        duration_seconds=None,
        published_at=None,
        language=None,
        tags=(),
        chapters=(),
        provider_details=None,
    )
    transcript = replace(snapshot.transcript, is_generated=True, segments=())
    lookup = replace(snapshot.exclusion_lookup, retrieved_at=None)
    snapshot = Source(
        metadata, transcript, lookup, filter_source_transcript(metadata, transcript, lookup)
    )
    monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: snapshot)
    cache = SourceCache(tmp_path / "sources.db")
    result = cache.ingest(URL, exclude_sponsors=False)
    assert result.source == snapshot
    assert SourceCache(cache.path).get(result.source_id) == snapshot


def test_corrupt_database_and_incompatible_schema_fail(tmp_path):
    database = tmp_path / "sources.db"
    database.write_bytes(b"not a SQLite database")
    with pytest.raises(SourceCacheError, match="Cannot open source cache"):
        SourceCache(database)
    database.unlink()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(SourceCacheError, match="Incompatible source cache schema"):
        SourceCache(database)


def test_concurrent_initialization_is_safe(tmp_path):
    database = tmp_path / "sources.db"
    barrier = Barrier(4)

    def initialize(_):
        barrier.wait()
        return SourceCache(database)

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(tuple(pool.map(initialize, range(4)))) == 4


def test_overlapping_miss_keeps_first_published_snapshot(tmp_path, extraction, monkeypatch):
    snapshot = ingest_source(URL)
    cache = SourceCache(tmp_path / "sources.db")
    winner = None

    def extract(url, exclude_sponsors=True):
        nonlocal winner
        monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: snapshot)
        winner = SourceCache(cache.path).ingest(URL)
        return replace(snapshot, metadata=replace(snapshot.metadata, title="Losing extraction"))

    monkeypatch.setattr(source_cache, "ingest_source", extract)
    result = cache.ingest(URL)
    assert result.cached
    assert result.source_id == winner.source_id
    assert result.source == snapshot


def test_xdg_default_and_relative_xdg_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_cache_path() == tmp_path / "obsidian-ingest/sources.sqlite3"
    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_cache_path() == tmp_path / ".local/state/obsidian-ingest/sources.sqlite3"
