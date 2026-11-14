"""Persistent current captures, independent of provider I/O and MCP tools.

The cache is disposable: each source/acquisition-option key holds only its current
capture. Data derived from a capture is keyed by ``capture_id`` with ON DELETE CASCADE,
so replacing a capture removes everything derived from the old one in the same
transaction.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from uuid import uuid4

from obsidian_ingest.extractor import Provider, detect_provider
from obsidian_ingest.extractor.acquisition import (
    Acquisition,
    CaptionFormat,
    CaptionTrack,
    OriginalCaption,
)
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
    NativeFormat,
    SourceMetadata,
    YouTubeMetadata,
)
from obsidian_ingest.extractor.providers.youtube import extract_video_id
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.source import Source, ingest_source

SCHEMA_VERSION = 2
_ROW = "source_id, provider, native_id, exclude_sponsors, payload, checksum"
_SCHEMA = (
    (
        "CREATE TABLE sources (source_id TEXT PRIMARY KEY, capture_id TEXT NOT NULL UNIQUE, "
        "provider TEXT NOT NULL, native_id TEXT NOT NULL, exclude_sponsors INTEGER NOT NULL, "
        "payload TEXT NOT NULL, checksum TEXT NOT NULL, "
        "UNIQUE(provider, native_id, exclude_sponsors))"
    ),
    # The selected original caption response, separate from normalized atoms.
    (
        "CREATE TABLE caption_payloads (capture_id TEXT PRIMARY KEY "
        "REFERENCES sources(capture_id) ON DELETE CASCADE, format TEXT NOT NULL, "
        "data BLOB NOT NULL, sha256 TEXT NOT NULL)"
    ),
)


class SourceCacheError(RuntimeError):
    """An unusable cache database or incomplete/incompatible extraction snapshot."""


@dataclass(frozen=True, slots=True)
class CachedSource:
    """``cached`` is True when an existing capture was reused instead of acquired."""

    source_id: str
    source: Source
    cached: bool


def default_cache_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state and Path(state).is_absolute() else Path.home() / ".local/state"
    return root / "obsidian-ingest/sources.sqlite3"


def reset_instructions(path: Path) -> str:
    return (
        f"The source cache is disposable. To recreate it, stop obsidian-ingest processes "
        f"using it and delete {path}; a new cache is created on next use. Vault notes are "
        "not affected, but existing source_id handles become unknown, so ingest URLs again."
    )


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _encode(source: Source) -> str:
    acquisition = source.acquisition
    return json.dumps(
        {
            "version": SCHEMA_VERSION,
            "acquisition": asdict(acquisition) if acquisition is not None else None,
            "metadata": asdict(source.metadata),
            "transcript": asdict(source.transcript),
            "exclusion_lookup": asdict(source.exclusion_lookup),
        },
        default=_json_default,
        allow_nan=False,
        sort_keys=True,
    )


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _decode_acquisition(data: dict | None) -> Acquisition | None:
    if data is None:
        return None
    caption = data["caption"]
    track = CaptionTrack(
        format=CaptionFormat(caption["format"]),
        is_translated=caption["is_translated"],
        payload_bytes=caption["payload_bytes"],
        payload_sha256=caption["payload_sha256"],
    )
    acquired_at = _date(data["acquired_at"])
    if acquired_at is None or acquired_at.tzinfo is None:
        raise ValueError("Missing acquisition time")
    if not isinstance(data["extractor"], str) or not data["extractor"]:
        raise ValueError("Missing extractor")
    version = data["extractor_version"]
    if version is not None and (not isinstance(version, str) or not version):
        raise ValueError("Invalid extractor version")
    if track.is_translated is not None and not isinstance(track.is_translated, bool):
        raise ValueError("Invalid caption translation status")
    size = track.payload_bytes
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("Invalid caption payload size")
    if not isinstance(track.payload_sha256, str) or len(track.payload_sha256) != 64:
        raise ValueError("Invalid caption payload checksum")
    return Acquisition(acquired_at, data["extractor"], version, track)


def _decode(payload: str) -> Source:
    data = json.loads(payload)
    if type(data["version"]) is not int or data["version"] != SCHEMA_VERSION:
        raise ValueError("Incompatible extraction snapshot version")
    acquisition = _decode_acquisition(data["acquisition"])
    metadata_data = dict(data["metadata"])
    metadata_data["provider"] = Provider(metadata_data["provider"])
    metadata_data["native_format"] = NativeFormat(metadata_data["native_format"])
    metadata_data["published_at"] = _date(metadata_data["published_at"])
    metadata_data["tags"] = tuple(metadata_data["tags"])
    metadata_data["chapters"] = tuple(Chapter(**chapter) for chapter in metadata_data["chapters"])
    details = metadata_data["provider_details"]
    metadata_data["provider_details"] = YouTubeMetadata(**details) if details is not None else None
    metadata = SourceMetadata(**metadata_data)
    transcript_data = dict(data["transcript"])
    transcript_data["segments"] = tuple(
        TranscriptSegment(**segment) for segment in transcript_data["segments"]
    )
    transcript = Transcript(**transcript_data)
    lookup_data = dict(data["exclusion_lookup"])
    lookup_data["ranges"] = tuple(ExcludedRange(**item) for item in lookup_data["ranges"])
    lookup_data["status"] = ExclusionStatus(lookup_data["status"])
    lookup_data["categories"] = tuple(lookup_data["categories"])
    lookup_data["retrieved_at"] = _date(lookup_data["retrieved_at"])
    lookup = ExclusionLookup(**lookup_data)

    # Dataclasses do not enforce annotations. Validate stored scalar types as well
    # as timings/identity before rebuilding the derived filtering view.
    required_strings = (
        metadata.source_id,
        metadata.original_url,
        metadata.canonical_url,
        transcript.video_id,
        transcript.language,
        transcript.language_code,
        lookup.provenance,
    )
    optional_strings = (
        metadata.title,
        metadata.description,
        metadata.creator_name,
        metadata.creator_id,
        metadata.creator_url,
        metadata.language,
        lookup.error_message,
    )
    if any(not isinstance(value, str) or not value for value in required_strings):
        raise ValueError("Invalid source identity or language")
    if any(value is not None and not isinstance(value, str) for value in optional_strings):
        raise ValueError("Invalid metadata text")
    if any(not isinstance(value, str) for value in (*metadata.tags, *lookup.categories)):
        raise ValueError("Invalid tags or exclusion categories")
    if any(not isinstance(item.title, str) for item in metadata.chapters):
        raise ValueError("Invalid chapter title")
    if any(not isinstance(item.text, str) for item in transcript.segments):
        raise ValueError("Invalid transcript text")
    if not isinstance(transcript.is_generated, bool):
        raise TypeError("Invalid caption generation flag")
    duration = metadata.duration_seconds
    if duration is not None and (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not isfinite(duration)
        or duration < 0
    ):
        raise ValueError("Invalid source duration")
    if details is not None:
        if any(
            details[key] is not None and not isinstance(details[key], str)
            for key in ("channel_handle", "playlist_id", "playlist_title", "live_status")
        ):
            raise ValueError("Invalid YouTube metadata text")
        if not isinstance(details["is_short"], bool) or (
            details["was_live"] is not None and not isinstance(details["was_live"], bool)
        ):
            raise ValueError("Invalid YouTube flags")
        index = details["playlist_index"]
        if index is not None and (isinstance(index, bool) or not isinstance(index, int)):
            raise ValueError("Invalid playlist index")
    source = Source(
        metadata,
        transcript,
        lookup,
        filter_source_transcript(metadata, transcript, lookup),
        acquisition,
    )
    # Reject missing/defaulted fields and unknown fields, not just invalid JSON.
    if json.loads(_encode(source)) != data:
        raise ValueError("Incomplete or incompatible extraction snapshot")
    return source


def _original_for_storage(source: Source) -> OriginalCaption | None:
    original = source.original_caption
    if original is None:
        return None
    track = source.acquisition.caption if source.acquisition is not None else None
    if (
        track is None
        or track.format != original.format
        or track.payload_bytes != len(original.data)
        or track.payload_sha256 != original.sha256
    ):
        raise ValueError("Original caption payload does not match its acquisition record")
    return original


class SourceCache:
    """Cache one current capture per option key; refresh retains the existing handle."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else default_cache_path()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
                if schema_version not in (0, SCHEMA_VERSION):
                    raise SourceCacheError(
                        f"Incompatible source cache schema version {schema_version} at "
                        f"{self.path}; this version requires {SCHEMA_VERSION}. "
                        f"{reset_instructions(self.path)}"
                    )
                if schema_version == 0:
                    tables = connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                    if tables:
                        raise SourceCacheError(
                            f"Unrecognized source cache schema at {self.path}. "
                            f"{reset_instructions(self.path)}"
                        )
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.execute(f"SELECT capture_id, {_ROW} FROM sources LIMIT 0")
                connection.execute(
                    "SELECT capture_id, format, data, sha256 FROM caption_payloads LIMIT 0"
                )
        except (OSError, sqlite3.Error) as error:
            raise SourceCacheError(
                f"Cannot open source cache {self.path}: {error}. {reset_instructions(self.path)}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _read(self, column: str, values: tuple) -> tuple | None:
        try:
            with closing(self._connect()) as connection:
                return connection.execute(
                    f"SELECT {_ROW} FROM sources WHERE {column}", values
                ).fetchone()
        except sqlite3.Error as error:
            raise SourceCacheError(f"Cannot read source cache {self.path}: {error}") from error

    def _result(self, row: tuple, *, cached: bool) -> CachedSource:
        source_id, provider, native_id, exclude_sponsors, payload, checksum = row
        try:
            if not isinstance(source_id, str) or not source_id:
                raise ValueError("Missing persistent source ID")
            if hashlib.sha256(payload.encode()).hexdigest() != checksum:
                raise ValueError("Snapshot checksum mismatch")
            source = _decode(payload)
            expected_categories = ("sponsor",) if exclude_sponsors else ()
            lookup = source.exclusion_lookup
            if (
                source.metadata.provider.value != provider
                or source.metadata.source_id != native_id
                or exclude_sponsors not in (0, 1)
                or lookup.categories != expected_categories
                or (lookup.status == ExclusionStatus.DISABLED) != (not exclude_sponsors)
                or any(item.reason not in expected_categories for item in lookup.ranges)
            ):
                raise ValueError("Snapshot identity or filtering options do not match cache key")
            return CachedSource(source_id, source, cached)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
            raise SourceCacheError(
                f"Invalid source cache entry {source_id}: {error}; "
                "ingest the URL with refresh=True to replace it"
            ) from error

    def get(self, source_id: str) -> Source:
        row = self._read("source_id = ?", (source_id,))
        if row is None:
            raise KeyError(source_id)
        return self._result(row, cached=True).source

    def original_caption(self, source_id: str) -> OriginalCaption | None:
        """Return the retained original caption response of the current capture, if any."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT sources.source_id, caption_payloads.format, caption_payloads.data, "
                    "caption_payloads.sha256 FROM sources LEFT JOIN caption_payloads "
                    "USING (capture_id) WHERE sources.source_id = ?",
                    (source_id,),
                ).fetchone()
        except sqlite3.Error as error:
            raise SourceCacheError(f"Cannot read source cache {self.path}: {error}") from error
        if row is None:
            raise KeyError(source_id)
        _, caption_format, data, sha256 = row
        if data is None:
            return None
        try:
            original = OriginalCaption(CaptionFormat(caption_format), bytes(data))
            if original.sha256 != sha256:
                raise ValueError("Original caption checksum mismatch")
        except (TypeError, ValueError) as error:
            raise SourceCacheError(
                f"Invalid original caption for {source_id}: {error}; "
                "ingest the URL with refresh=True to replace it"
            ) from error
        return original

    def ingest(
        self, url: str, *, exclude_sponsors: bool = True, refresh: bool = False
    ) -> CachedSource:
        """Reuse the current capture, or acquire one; refresh replaces it only on success.

        A failed acquisition raises and leaves any existing capture untouched.
        """
        if not isinstance(exclude_sponsors, bool) or not isinstance(refresh, bool):
            raise TypeError("exclude_sponsors and refresh must be booleans")
        provider = detect_provider(url)
        if provider != Provider.YOUTUBE:
            raise ValueError(f"Source ingestion is not supported for provider: {provider}")
        native_id = extract_video_id(url)
        key = (provider.value, native_id, int(exclude_sponsors))
        predicate = "provider = ? AND native_id = ? AND exclude_sponsors = ?"
        row = self._read(predicate, key)
        if row is not None and not refresh:
            return self._result(row, cached=True)

        # Build and validate the complete new capture before touching the database.
        source = ingest_source(url, exclude_sponsors=exclude_sponsors)
        original = _original_for_storage(source)
        payload = _encode(source)
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        new_id = uuid4().hex
        result = self._result((new_id, *key, payload, checksum), cached=False)
        capture_id = uuid4().hex
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    f"SELECT {_ROW} FROM sources WHERE {predicate}", key
                ).fetchone()
                if existing is not None and not refresh:
                    # Another process published while extraction was in flight; keep it.
                    return self._result(existing, cached=True)
                source_id = existing[0] if existing is not None else new_id
                if existing is not None:
                    # Cascades to every row derived from the old capture.
                    connection.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
                connection.execute(
                    "INSERT INTO sources (source_id, capture_id, provider, native_id, "
                    "exclude_sponsors, payload, checksum) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (source_id, capture_id, *key, payload, checksum),
                )
                if original is not None:
                    connection.execute(
                        "INSERT INTO caption_payloads VALUES (?, ?, ?, ?)",
                        (capture_id, original.format.value, original.data, original.sha256),
                    )
        except sqlite3.Error as error:
            raise SourceCacheError(f"Cannot write source cache {self.path}: {error}") from error
        return CachedSource(source_id, result.source, cached=False)
