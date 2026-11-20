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
from obsidian_ingest.extractor.acquisition import CaptionError, CaptionFormat, OriginalCaption
from obsidian_ingest.extractor.metadata import ExcludedRange, ExclusionLookup, ExclusionStatus
from obsidian_ingest.extractor.providers import youtube
from obsidian_ingest.extractor.providers.youtube import sponsorblock
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.reading import issue_token, token_progress
from obsidian_ingest.source import Source, ingest_source
from obsidian_ingest.source_cache import SourceCache, SourceCacheError, default_cache_path

URL = "https://www.youtube.com/watch?v=video-123"
CAPTIONS = json.dumps(
    {
        "events": [
            {"tStartMs": 125, "dDurationMs": 2000, "segs": [{"utf8": "Original\nwords"}]},
            {"tStartMs": 1000, "dDurationMs": 500, "segs": [{"utf8": "Sponsor"}]},
            {"tStartMs": 1000, "dDurationMs": 100, "segs": [{"utf8": "Same start"}]},
            {"tStartMs": 2125, "dDurationMs": 750, "segs": [{"utf8": "Next"}]},
        ]
    }
).encode()
VTT = b"WEBVTT\n\n00:00.125 --> 00:02.125\nOriginal\nwords\n\n00:02.125 --> 00:02.875\nNext\n"


def test_refresh_with_identical_rows_invalidates_read_token(tmp_path, extraction):
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    token = issue_token(first.source, 2)
    refreshed = cache.ingest(URL, refresh=True)
    assert refreshed.source_id == first.source_id
    assert refreshed.source.transcript == first.source.transcript
    with pytest.raises(ValueError, match="source changed"):
        token_progress(refreshed.source, token)


def test_republishing_identical_payload_invalidates_read_token(tmp_path, extraction, monkeypatch):
    cache = SourceCache(tmp_path / "sources.db")
    acquired = ingest_source(URL)
    assert acquired.acquisition is not None
    monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: acquired)
    first = cache.ingest(URL)
    token = issue_token(first.source, 2)
    assert token_progress(cache.get(first.source_id), token) == 2
    before = _rows(cache.path, "SELECT capture_id, payload, checksum FROM sources")

    refreshed = cache.ingest(URL, refresh=True)
    after = _rows(cache.path, "SELECT capture_id, payload, checksum FROM sources")
    assert refreshed.source_id == first.source_id
    assert after[0][0] != before[0][0]
    assert after[0][1:] == before[0][1:]
    assert token_progress(cache.get(first.source_id), issue_token(refreshed.source, 2)) == 2
    with pytest.raises(ValueError, match="source changed"):
        token_progress(cache.get(first.source_id), token)


def test_cache_round_trips_using_provider_codec(monkeypatch, sample_source):
    details = sample_source.metadata.provider_details

    class FakeProvider:
        def encode_details(self, value):
            assert value == details
            return {"custom": "encoding"}

        def decode_details(self, data):
            assert data == {"custom": "encoding"}
            return details

    monkeypatch.setattr(source_cache, "get_provider", lambda _: FakeProvider())
    payload = source_cache._encode(sample_source)
    assert json.loads(payload)["metadata"]["provider_details"] == {"custom": "encoding"}
    assert source_cache._decode(payload) == sample_source


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
    ydl.urlopen.return_value.read.return_value = CAPTIONS

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
        caption_rows = connection.execute("SELECT format, data FROM original_payloads").fetchall()
    assert payload["acquisition"]["extractor"] == "yt-dlp"
    assert payload["acquisition"]["extractor_version"]
    assert datetime.fromisoformat(payload["acquisition"]["acquired_at"]).tzinfo is not None
    assert payload["acquisition"]["caption"]["format"] == "json3"
    assert set(payload) == {"version", "acquisition", "metadata", "transcript", "exclusion_lookup"}
    # The original response is stored once, separately from normalized atoms.
    assert caption_rows == [("json3", CAPTIONS)]
    assert "events" not in json.dumps(payload)
    original = restarted.original_caption(first.source_id)
    assert original == OriginalCaption(CaptionFormat.JSON3, CAPTIONS)
    assert hit.source.original_caption is None


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


def _rows(path, query):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(query).fetchall()


def test_refresh_replaces_capture_and_cascades_derived_rows(tmp_path, extraction):
    _, ydl, _ = extraction
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    (old_capture,) = _rows(cache.path, "SELECT capture_id FROM sources")[0]
    # Stand-in for future capture-derived data (corrections/images); not a product table.
    with closing(sqlite3.connect(cache.path)) as connection, connection:
        connection.execute(
            "CREATE TABLE derived (capture_id TEXT NOT NULL "
            "REFERENCES sources(capture_id) ON DELETE CASCADE)"
        )
        connection.execute("INSERT INTO derived VALUES (?)", (old_capture,))
    ydl.extract_info.return_value["subtitles"] = {
        "en-US": [{"ext": "vtt", "url": "https://example.com/vtt"}]
    }
    ydl.urlopen.return_value.read.return_value = VTT
    refreshed = cache.ingest(URL, refresh=True)

    assert refreshed.source_id == first.source_id
    (new_capture,) = _rows(cache.path, "SELECT capture_id FROM sources")[0]
    assert new_capture != old_capture
    assert _rows(cache.path, "SELECT count(*) FROM sources") == [(1,)]
    assert _rows(cache.path, "SELECT capture_id, format FROM original_payloads") == [
        (new_capture, "vtt")
    ]
    assert _rows(cache.path, "SELECT * FROM derived") == []
    assert cache.original_caption(first.source_id) == OriginalCaption(CaptionFormat.VTT, VTT)
    assert cache.get(first.source_id).acquisition.caption.format == CaptionFormat.VTT


@pytest.mark.parametrize("failure", ["extraction", "empty", "malformed", "unsupported"])
def test_failed_refresh_keeps_previous_capture_and_payload(tmp_path, extraction, failure):
    _, ydl, _ = extraction
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    before = (
        _rows(cache.path, "SELECT * FROM sources"),
        _rows(cache.path, "SELECT * FROM original_payloads"),
    )
    if failure == "extraction":
        ydl.extract_info.side_effect = RuntimeError("Network down")
        expected = RuntimeError
    elif failure == "empty":
        ydl.urlopen.return_value.read.return_value = b""
        expected = CaptionError
    elif failure == "malformed":
        ydl.urlopen.return_value.read.return_value = b"{not json"
        expected = CaptionError
    else:
        ydl.extract_info.return_value["subtitles"] = {"en": [{"ext": "srv3", "url": "u"}]}
        expected = CaptionError
    with pytest.raises(expected):
        cache.ingest(URL, refresh=True)
    after = (
        _rows(cache.path, "SELECT * FROM sources"),
        _rows(cache.path, "SELECT * FROM original_payloads"),
    )
    assert after == before
    assert cache.get(first.source_id) == first.source
    assert cache.original_caption(first.source_id).data == CAPTIONS


def test_vtt_only_source_ingests_and_round_trips(tmp_path, extraction):
    _, ydl, _ = extraction
    ydl.extract_info.return_value["subtitles"] = {
        "en-US": [
            {"ext": "srv1", "url": "https://example.com/srv1"},
            {"ext": "vtt", "url": "https://example.com/vtt?signature=secret"},
        ]
    }
    ydl.urlopen.return_value.read.return_value = VTT
    cache = SourceCache(tmp_path / "sources.db")
    result = cache.ingest(URL, exclude_sponsors=False)
    segments = result.source.transcript.segments
    assert [(item.text, item.start, item.duration) for item in segments] == [
        ("Original\nwords", 0.125, 2.0),
        ("Next", 2.125, 0.75),
    ]
    ydl.urlopen.assert_called_once_with("https://example.com/vtt?signature=secret")
    assert SourceCache(cache.path).get(result.source_id) == result.source
    (stored,) = _rows(cache.path, "SELECT payload FROM sources")[0]
    assert "signature=secret" not in stored


def test_source_without_recorded_acquisition_stores_no_payload(tmp_path, extraction, monkeypatch):
    snapshot = replace(ingest_source(URL), acquisition=None, original_caption=None)
    monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: snapshot)
    cache = SourceCache(tmp_path / "sources.db")
    result = cache.ingest(URL)
    assert result.source.acquisition is None
    assert cache.original_caption(result.source_id) is None
    with pytest.raises(KeyError):
        cache.original_caption("missing")


def test_mismatched_original_payload_is_rejected_before_writing(tmp_path, extraction, monkeypatch):
    snapshot = ingest_source(URL)
    forged = replace(snapshot, original_caption=OriginalCaption(CaptionFormat.JSON3, b"{}"))
    monkeypatch.setattr(source_cache, "ingest_source", lambda *args, **kwargs: forged)
    cache = SourceCache(tmp_path / "sources.db")
    with pytest.raises(ValueError, match="does not match"):
        cache.ingest(URL)
    assert _rows(cache.path, "SELECT count(*) FROM sources") == [(0,)]


def test_corrupt_original_payload_fails_explicitly(tmp_path, extraction):
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    with closing(sqlite3.connect(cache.path)) as connection, connection:
        connection.execute("UPDATE original_payloads SET data = ?", (b"tampered",))
    with pytest.raises(SourceCacheError, match="Invalid original caption.*refresh=True"):
        cache.original_caption(first.source_id)


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


def _set(*path_and_value):
    """Return a payload mutation that sets the nested field at ``path`` to ``value``."""
    *path, value = path_and_value

    def mutate(data: dict) -> None:
        target = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _delete_segments(data: dict) -> None:
    del data["transcript"]["segments"]


_CORRUPTIONS = {
    "checksum": None,
    "json": None,
    "missing": _delete_segments,
    "version": _set("version", 99),
    "version-type": _set("version", 3.0),
    "type": _set("transcript", "segments", 0, "text", 123),
    "category": _set("exclusion_lookup", "ranges", 0, "reason", "intro"),
    "timing": _set("transcript", "segments", 0, "start", -1),
    "identity": _set("transcript", "source_id", "another-video"),
    "empty-identity": _set("metadata", "source_id", ""),
    "metadata-text": _set("metadata", "title", 5),
    "tags": _set("metadata", "tags", [1]),
    "chapter-title": _set("metadata", "chapters", 0, "title", None),
    "generated-flag": _set("transcript", "is_generated", "yes"),
    "duration": _set("metadata", "duration_seconds", -1),
    "duration-type": _set("metadata", "duration_seconds", True),
    "youtube-text": _set("metadata", "provider_details", "channel_handle", 5),
    "youtube-flags": _set("metadata", "provider_details", "is_short", "no"),
    "playlist-index": _set("metadata", "provider_details", "playlist_index", "2"),
    "acquired-at-naive": _set("acquisition", "acquired_at", "2026-01-02T03:04:05"),
    "acquired-at-missing": _set("acquisition", "acquired_at", None),
    "extractor": _set("acquisition", "extractor", ""),
    "extractor-version": _set("acquisition", "extractor_version", 7),
    "translated": _set("acquisition", "caption", "is_translated", "no"),
    "payload-size": _set("acquisition", "caption", "payload_bytes", 0),
    "payload-sha": _set("acquisition", "caption", "payload_sha256", "short"),
    "caption-format": _set("acquisition", "caption", "format", "srv3"),
    "unknown-field": _set("metadata", "unexpected", 1),
}


@pytest.mark.parametrize("corruption", list(_CORRUPTIONS))
def test_corrupt_entries_fail_explicitly_and_refresh_recovers(tmp_path, extraction, corruption):
    _, ydl, _ = extraction
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    with closing(sqlite3.connect(cache.path)) as connection, connection:
        payload = connection.execute("SELECT payload FROM sources").fetchone()[0]
        data = json.loads(payload)
        if (mutate := _CORRUPTIONS[corruption]) is not None:
            mutate(data)
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
    assert replace(recovered.source, acquisition=None) == replace(first.source, acquisition=None)
    assert recovered.source.acquisition.acquired_at >= first.source.acquisition.acquired_at


def test_missing_source_id_is_explicit(tmp_path):
    with pytest.raises(KeyError):
        SourceCache(tmp_path / "sources.db").get("missing")


def test_ingest_rejects_non_boolean_options_and_other_providers_without_extraction(
    tmp_path, extraction
):
    factory, _, _ = extraction
    cache = SourceCache(tmp_path / "sources.db")
    for options in ({"exclude_sponsors": 1}, {"refresh": "false"}):
        with pytest.raises(TypeError, match="must be booleans"):
            cache.ingest(URL, **options)
    with pytest.raises(ValueError, match="not supported for provider"):
        cache.ingest("https://www.tiktok.com/@user/video/123")
    factory.assert_not_called()
    assert _rows(cache.path, "SELECT count(*) FROM sources") == [(0,)]


def test_unreadable_cache_tables_are_cache_errors(tmp_path, extraction):
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    with closing(sqlite3.connect(cache.path)) as connection, connection:
        connection.execute("DROP TABLE original_payloads")
    with pytest.raises(SourceCacheError, match="Cannot read source cache"):
        cache.original_caption(first.source_id)
    with closing(sqlite3.connect(cache.path)) as connection, connection:
        connection.execute("DROP TABLE sources")
    with pytest.raises(SourceCacheError, match="Cannot read source cache"):
        cache.get(first.source_id)


def test_failed_cache_write_rolls_back_the_whole_capture(tmp_path, extraction):
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    before = _rows(cache.path, "SELECT * FROM sources")
    # The replacement fails on its second statement; the delete and insert must not stick.
    with closing(sqlite3.connect(cache.path)) as connection, connection:
        connection.execute(
            "CREATE TRIGGER reject_payload BEFORE INSERT ON original_payloads "
            "BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END"
        )
    with pytest.raises(SourceCacheError, match="Cannot write source cache.*simulated"):
        cache.ingest(URL, refresh=True)
    assert _rows(cache.path, "SELECT * FROM sources") == before
    assert cache.original_caption(first.source_id).data == CAPTIONS
    with pytest.raises(SourceCacheError, match="Cannot write source cache"):
        cache.ingest(URL, exclude_sponsors=False)
    assert _rows(cache.path, "SELECT count(*) FROM sources") == [(1,)]


def test_entry_without_a_persistent_id_is_invalid(tmp_path, extraction):
    cache = SourceCache(tmp_path / "sources.db")
    first = cache.ingest(URL)
    with closing(sqlite3.connect(cache.path)) as connection, connection:
        connection.execute("UPDATE sources SET source_id = ''")
    with pytest.raises(SourceCacheError, match="Missing persistent source ID"):
        cache.ingest(URL)
    with pytest.raises(KeyError):
        cache.get(first.source_id)


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


def test_previous_schema_cache_is_kept_and_explains_reset(tmp_path):
    database = tmp_path / "sources.db"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE sources (source_id TEXT PRIMARY KEY, provider TEXT NOT NULL, "
            "native_id TEXT NOT NULL, exclude_sponsors INTEGER NOT NULL, "
            "payload TEXT NOT NULL, checksum TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO sources VALUES ('old', 'youtube', 'v', 1, '{}', 'x')")
        connection.execute("PRAGMA user_version = 1")
    with pytest.raises(SourceCacheError) as raised:
        SourceCache(database)
    message = str(raised.value)
    assert "schema version 1" in message
    assert f"delete {database}" in message
    assert "Vault notes are not affected" in message
    assert _rows(database, "SELECT source_id FROM sources") == [("old",)]

    other = tmp_path / "other.db"
    with closing(sqlite3.connect(other)) as connection:
        connection.execute("CREATE TABLE unrelated (x)")
    with pytest.raises(SourceCacheError, match=f"Unrecognized source cache schema.*delete {other}"):
        SourceCache(other)
    assert _rows(other, "SELECT count(*) FROM unrelated") == [(0,)]


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
