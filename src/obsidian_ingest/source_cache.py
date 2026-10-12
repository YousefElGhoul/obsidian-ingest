"""Persistent extraction snapshots, independent of provider I/O and MCP tools."""

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from math import isfinite
from pathlib import Path
from uuid import uuid4

from obsidian_ingest.extractor import Provider, detect_provider
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

SCHEMA_VERSION = 1


class SourceCacheError(RuntimeError):
    """An unusable cache database or incomplete/incompatible extraction snapshot."""


@dataclass(frozen=True, slots=True)
class CachedSource:
    source_id: str
    source: Source
    cached: bool


def default_cache_path() -> Path:
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state and Path(state).is_absolute() else Path.home() / ".local/state"
    return root / "obsidian-ingest/sources.sqlite3"


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _encode(source: Source, extracted_at: datetime, extractor_version: str) -> str:
    return json.dumps(
        {
            "version": SCHEMA_VERSION,
            "extraction": {
                "extracted_at": extracted_at.isoformat(),
                "extractor": "yt-dlp",
                "extractor_version": extractor_version,
            },
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


def _decode(payload: str) -> Source:
    data = json.loads(payload)
    if type(data["version"]) is not int or data["version"] != SCHEMA_VERSION:
        raise ValueError("Incompatible extraction snapshot version")
    extraction = data["extraction"]
    extracted_at = _date(extraction["extracted_at"])
    if extracted_at is None or extraction["extractor"] != "yt-dlp":
        raise ValueError("Missing extraction metadata")
    if not isinstance(extraction["extractor_version"], str) or not extraction["extractor_version"]:
        raise ValueError("Missing extractor version")
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
        metadata, transcript, lookup, filter_source_transcript(metadata, transcript, lookup)
    )
    # Reject missing/defaulted fields and unknown fields, not just invalid JSON.
    if json.loads(_encode(source, extracted_at, extraction["extractor_version"])) != data:
        raise ValueError("Incomplete or incompatible extraction snapshot")
    return source


class SourceCache:
    """Cache immutable option-specific handles; refresh retains the existing handle."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else default_cache_path()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
                if schema_version not in (0, SCHEMA_VERSION):
                    raise SourceCacheError(f"Incompatible source cache schema: {schema_version}")
                if schema_version == 0:
                    tables = connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                    if tables:
                        raise SourceCacheError("Unrecognized source cache schema")
                    connection.execute(
                        "CREATE TABLE sources (source_id TEXT PRIMARY KEY, provider TEXT NOT NULL, "
                        "native_id TEXT NOT NULL, exclude_sponsors INTEGER NOT NULL, "
                        "payload TEXT NOT NULL, checksum TEXT NOT NULL, "
                        "UNIQUE(provider, native_id, exclude_sponsors))"
                    )
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                connection.execute("SELECT source_id, payload, checksum FROM sources LIMIT 0")
        except (OSError, sqlite3.Error) as error:
            raise SourceCacheError(f"Cannot open source cache {self.path}: {error}") from error

    def _read(self, column: str, values: tuple) -> tuple | None:
        try:
            with closing(sqlite3.connect(self.path)) as connection:
                return connection.execute(
                    "SELECT source_id, provider, native_id, exclude_sponsors, payload, checksum "
                    f"FROM sources WHERE {column}",
                    values,
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

    def ingest(
        self, url: str, *, exclude_sponsors: bool = True, refresh: bool = False
    ) -> CachedSource:
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

        source = ingest_source(url, exclude_sponsors=exclude_sponsors)
        payload = _encode(source, datetime.now(UTC), version("yt-dlp"))
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        result = self._result((uuid4().hex, *key, payload, checksum), cached=False)
        try:
            with closing(sqlite3.connect(self.path)) as connection, connection:
                # Another process may have published while extraction was in flight.
                # Only explicit refresh may replace its snapshot; preserve its ID.
                conflict = (
                    "DO UPDATE SET payload = excluded.payload, checksum = excluded.checksum"
                    if refresh
                    else "DO NOTHING"
                )
                inserted = connection.execute(
                    "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?) "
                    f"ON CONFLICT(provider, native_id, exclude_sponsors) {conflict} RETURNING source_id",
                    (result.source_id, *key, payload, checksum),
                ).fetchone()
                row = connection.execute(
                    "SELECT source_id, provider, native_id, exclude_sponsors, payload, checksum "
                    f"FROM sources WHERE {predicate}",
                    key,
                ).fetchone()
        except sqlite3.Error as error:
            raise SourceCacheError(f"Cannot write source cache {self.path}: {error}") from error
        return self._result(row, cached=inserted is None)
