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
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from math import isfinite
from pathlib import Path
from uuid import uuid4

from obsidian_ingest.extractor import Provider, SourceKind
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
)
from obsidian_ingest.extractor.providers import (
    NotModified,
    get_provider,
    required_note,
    resolve_note,
    supported_provider,
)
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.notes import ClippedNote, NoteRef
from obsidian_ingest.source import Source, ingest_source, source_from_content

SCHEMA_VERSION = 4
_ROW = "source_id, capture_id, provider, native_id, exclude_sponsors, payload, checksum"
_KEY = "provider = ? AND native_id = ? AND exclude_sponsors = ?"
# Stable source handles identify option keys; capture IDs identify replaceable evidence.
_SCHEMA = (
    (
        "CREATE TABLE sources (source_id TEXT PRIMARY KEY, capture_id TEXT NOT NULL UNIQUE, "
        "provider TEXT NOT NULL, native_id TEXT NOT NULL, exclude_sponsors INTEGER NOT NULL, "
        "payload TEXT NOT NULL, checksum TEXT NOT NULL, content_sha256 TEXT NOT NULL, "
        "UNIQUE(provider, native_id, exclude_sponsors))"
    ),
    "CREATE INDEX sources_content ON sources(content_sha256)",
    # A requested URL whose page named another URL of the same site as its canonical one.
    (
        "CREATE TABLE aliases (provider TEXT NOT NULL, requested_id TEXT NOT NULL, "
        "native_id TEXT NOT NULL, PRIMARY KEY(provider, requested_id))"
    ),
    # The original acquired response (captions, page HTML), separate from normalized atoms.
    (
        "CREATE TABLE original_payloads (capture_id TEXT PRIMARY KEY "
        "REFERENCES sources(capture_id) ON DELETE CASCADE, format TEXT NOT NULL, "
        "data BLOB NOT NULL, sha256 TEXT NOT NULL)"
    ),
)


class SourceCacheError(RuntimeError):
    """An unusable cache database or incomplete/incompatible extraction snapshot."""


@dataclass(frozen=True, slots=True)
class CachedSource:
    """``cached`` is True when an existing capture was reused instead of acquired;
    ``not_modified`` when a refresh found the source unchanged and kept the capture."""

    source_id: str
    source: Source
    cached: bool
    not_modified: bool = False


def default_cache_path() -> Path:
    """Locate disposable acquisition state, ignoring relative XDG state roots."""
    state = os.environ.get("XDG_STATE_HOME")
    root = Path(state) if state and Path(state).is_absolute() else Path.home() / ".local/state"
    return root / "obsidian-ingest/sources.sqlite3"


def reset_instructions(path: Path) -> str:
    """Describe explicit cache recovery without suggesting any vault-note changes."""
    return (
        f"The source cache is disposable. To recreate it, stop obsidian-ingest processes "
        f"using it and delete {path}; a new cache is created on next use. Vault notes are "
        "not affected, but existing source_id handles become unknown, so ingest URLs again."
    )


# Snapshot serialization and validation


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _encode(source: Source) -> str:
    """Serialize the normalized capture; original caption bytes live in a separate table."""
    acquisition = source.acquisition
    metadata = asdict(replace(source.metadata, provider_details=None))
    metadata["provider_details"] = get_provider(source.metadata.provider).encode_details(
        source.metadata.provider_details
    )
    return json.dumps(
        {
            "version": SCHEMA_VERSION,
            "acquisition": asdict(acquisition) if acquisition is not None else None,
            "metadata": metadata,
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
    """Restore optional provenance and reject malformed caption identity fields."""
    if data is None:
        return None
    evidence = data["evidence"]
    if evidence is not None and not isinstance(evidence, dict):
        raise ValueError("Invalid acquisition evidence")
    caption = data["caption"]
    acquired_at = _date(data["acquired_at"])
    if acquired_at is None or acquired_at.tzinfo is None:
        raise ValueError("Missing acquisition time")
    if not isinstance(data["extractor"], str) or not data["extractor"]:
        raise ValueError("Missing extractor")
    version = data["extractor_version"]
    if version is not None and (not isinstance(version, str) or not version):
        raise ValueError("Invalid extractor version")
    if caption is None:
        return Acquisition(acquired_at, data["extractor"], version, None, evidence)
    track = CaptionTrack(
        format=CaptionFormat(caption["format"]),
        is_translated=caption["is_translated"],
        payload_bytes=caption["payload_bytes"],
        payload_sha256=caption["payload_sha256"],
    )
    if track.is_translated is not None and not isinstance(track.is_translated, bool):
        raise ValueError("Invalid caption translation status")
    size = track.payload_bytes
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError("Invalid caption payload size")
    if not isinstance(track.payload_sha256, str) or len(track.payload_sha256) != 64:
        raise ValueError("Invalid caption payload checksum")
    return Acquisition(acquired_at, data["extractor"], version, track, evidence)


def _decode(payload: str) -> Source:
    """Rebuild and validate a snapshot, deriving filtering from retained source atoms."""
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
    metadata_data["provider_details"] = get_provider(metadata_data["provider"]).decode_details(
        details
    )
    metadata = SourceMetadata(**metadata_data)
    transcript_data = dict(data["transcript"])
    transcript_data["segments"] = tuple(
        TranscriptSegment(**segment) for segment in transcript_data["segments"]
    )
    transcript_data["kind"] = SourceKind(transcript_data["kind"])
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
        transcript.source_id,
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
    """Reject original bytes that disagree with the capture's provenance record."""
    original = source.original_caption
    if original is None:
        return None
    acquisition = source.acquisition
    track = acquisition.caption if acquisition is not None else None
    if track is None and acquisition is not None and acquisition.evidence:
        # Text sources record their original payload's identity in their evidence.
        evidence = acquisition.evidence
        if (
            evidence.get("original_payload_format") != original.format.value
            or evidence.get("original_payload_bytes") != len(original.data)
            or evidence.get("original_payload_sha256") != original.sha256
        ):
            raise ValueError("Original payload does not match its acquisition record")
        return original
    if (
        track is None
        or track.format != original.format
        or track.payload_bytes != len(original.data)
        or track.payload_sha256 != original.sha256
    ):
        raise ValueError("Original caption payload does not match its acquisition record")
    return original


# Persistent capture access


class SourceCache:
    """Cache one current capture per option key; refresh retains the existing handle."""

    def __init__(self, path: Path | None = None) -> None:
        """Open or initialize the schema; incompatible databases require an explicit reset."""
        self.path = path if path is not None else default_cache_path()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path)) as connection, connection:
                # Serialize schema initialization with other writers, without migrating data.
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
                connection.execute(f"SELECT {_ROW}, content_sha256 FROM sources LIMIT 0")
                connection.execute("SELECT provider, requested_id, native_id FROM aliases LIMIT 0")
                connection.execute(
                    "SELECT capture_id, format, data, sha256 FROM original_payloads LIMIT 0"
                )
        except (OSError, sqlite3.Error) as error:
            raise SourceCacheError(
                f"Cannot open source cache {self.path}: {error}. {reset_instructions(self.path)}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        """Enable capture-dependent deletion on every operational connection."""
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _read(self, column: str, values: tuple) -> tuple | None:
        """Read with an internal SQL predicate and separately bound lookup values."""
        try:
            with closing(self._connect()) as connection:
                return connection.execute(
                    f"SELECT {_ROW} FROM sources WHERE {column}", values
                ).fetchone()
        except sqlite3.Error as error:
            raise SourceCacheError(f"Cannot read source cache {self.path}: {error}") from error

    def _result(self, row: tuple, *, cached: bool) -> CachedSource:
        """Check payload integrity and option-key agreement before exposing cached evidence."""
        source_id, capture_id, provider, native_id, exclude_sponsors, payload, checksum = row
        try:
            if not isinstance(source_id, str) or not source_id:
                raise ValueError("Missing persistent source ID")
            if not isinstance(capture_id, str) or not capture_id:
                raise ValueError("Missing capture ID")
            if hashlib.sha256(payload.encode()).hexdigest() != checksum:
                raise ValueError("Snapshot checksum mismatch")
            source = _decode(payload)
            expected_categories = get_provider(provider).exclusion_categories(
                bool(exclude_sponsors)
            )
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
            return CachedSource(source_id, replace(source, capture=capture_id), cached)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as error:
            raise SourceCacheError(
                f"Invalid source cache entry {source_id}: {error}; "
                "ingest the URL with refresh=True to replace it"
            ) from error

    def get(self, source_id: str) -> Source:
        """Return a validated capture, raising KeyError for an unknown persistent handle."""
        row = self._read("source_id = ?", (source_id,))
        if row is None:
            raise KeyError(source_id)
        return self._result(row, cached=True).source

    def original_caption(self, source_id: str) -> OriginalCaption | None:
        """Return the retained original caption response of the current capture, if any."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT sources.source_id, original_payloads.format, original_payloads.data, "
                    "original_payloads.sha256 FROM sources LEFT JOIN original_payloads "
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
        self,
        url: str,
        *,
        exclude_sponsors: bool = True,
        refresh: bool = False,
        note_readable: Callable[[NoteRef], bool] | None = None,
    ) -> CachedSource:
        """Reuse the current capture, or acquire one; refresh replaces it only on success.

        A failed acquisition raises and leaves any existing capture untouched.
        """
        if not isinstance(exclude_sponsors, bool) or not isinstance(refresh, bool):
            raise TypeError("exclude_sponsors and refresh must be booleans")
        provider = supported_provider(url)
        requested = provider.identity(url)
        native_id = self._alias(provider.name.value, requested) or requested
        # Aliased URLs share a capture; exclusion filtering is part of acquisition identity.
        key = (provider.name.value, native_id, int(exclude_sponsors))

        def reusable(source: Source, stored: str) -> bool:
            note = required_note(source)
            readable = note is None or (note_readable is not None and note_readable(note))
            return readable and _serves(provider, source, requested, stored)

        row = self._read(_KEY, key)
        previous = None
        if row is not None and not refresh:
            current = self._result(row, cached=True)
            if reusable(current.source, native_id):
                return current
        elif row is not None:
            # A refresh is conditional on the capture this request would otherwise reuse;
            # an unreadable entry is simply replaced.
            try:
                current = self._result(row, cached=True)
            except SourceCacheError:
                current = None
            if current is not None and reusable(current.source, native_id):
                previous = current

        # Build and validate the complete new capture before touching the database.
        try:
            source = ingest_source(
                url,
                exclude_sponsors=exclude_sponsors,
                previous=previous.source if previous is not None else None,
            )
        except NotModified:
            assert previous is not None
            # Another ingest may have replaced the capture while this one was fetching.
            row = self._read(_KEY, key)
            current = self._result(row, cached=True) if row is not None else None
            if current is not None and current.source.capture == previous.source.capture:
                return replace(current, not_modified=True)
            source = ingest_source(url, exclude_sponsors=exclude_sponsors)
        return self._publish(
            source,
            exclude_sponsors,
            requested=requested,
            replace=refresh,
            reuse=None if refresh else reusable,
        )

    def ingest_note(self, note: ClippedNote, *, exclude_sponsors: bool = True) -> CachedSource:
        """Ingest a saved note (a web clip) into the same capture key as its source URL.

        The clip is an explicit user choice, so it replaces whatever capture the key holds
        (a web capture, or a clip of an older revision); an unchanged clip is reused.
        Replacing the capture invalidates read tokens issued for the previous one.
        """
        if not isinstance(exclude_sponsors, bool):
            raise TypeError("exclude_sponsors must be a boolean")
        provider = resolve_note(note.text)
        requested = provider.note_identity(note)
        key = (provider.name.value, requested, int(exclude_sponsors))

        def reusable(source: Source, stored: str) -> bool:
            return provider.capture_matches_note(source, note)

        row = self._read(_KEY, key)
        if row is not None:
            current = self._result(row, cached=True)
            if reusable(current.source, requested):
                return current

        # The clip owns its source URL's key, so publishing drops any alias of that URL.
        content = provider.acquire_note(note, exclude_sponsors=exclude_sponsors)
        return self._publish(
            source_from_content(content),
            exclude_sponsors,
            requested=requested,
            replace=True,
            reuse=reusable,
        )

    def _alias(self, provider: str, requested: str) -> str | None:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT native_id FROM aliases WHERE provider = ? AND requested_id = ?",
                    (provider, requested),
                ).fetchone()
        except sqlite3.Error as error:
            raise SourceCacheError(f"Cannot read source cache {self.path}: {error}") from error
        return row[0] if row else None

    def same_content(self, source_id: str) -> tuple[str, ...]:
        """Other captures with exactly the same text, reported and never merged."""
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT other.source_id FROM sources AS this JOIN sources AS other "
                    "ON other.content_sha256 = this.content_sha256 "
                    "AND other.source_id != this.source_id "
                    "WHERE this.source_id = ? ORDER BY other.source_id",
                    (source_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise SourceCacheError(f"Cannot read source cache {self.path}: {error}") from error
        return tuple(row[0] for row in rows)

    def _publish(
        self,
        source: Source,
        exclude_sponsors: bool,
        *,
        requested: str,
        replace: bool,
        reuse: Callable[[Source, str], bool] | None = None,
    ) -> CachedSource:
        """Store a validated capture under its own identity, keeping the persistent source_id.

        The capture's identity can differ from the requested one (a page naming its
        canonical URL); the request then becomes an alias of it, in the same transaction.
        """
        provider = source.metadata.provider.value
        stored = source.metadata.source_id
        key = (provider, stored, int(exclude_sponsors))
        original = _original_for_storage(source)
        payload = _encode(source)
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        content = _content_hash(source)
        new_id = uuid4().hex
        capture_id = uuid4().hex
        result = self._result((new_id, capture_id, *key, payload, checksum), cached=False)
        try:
            with closing(self._connect()) as connection, connection:
                # Deletion and replacement commit together; any write failure rolls both back.
                connection.execute("BEGIN IMMEDIATE")
                if stored == requested:
                    connection.execute(
                        "DELETE FROM aliases WHERE provider = ? AND requested_id = ?",
                        (provider, requested),
                    )
                else:
                    connection.execute(
                        "INSERT OR REPLACE INTO aliases VALUES (?, ?, ?)",
                        (provider, requested, stored),
                    )
                existing = connection.execute(
                    f"SELECT {_ROW} FROM sources WHERE {_KEY}", key
                ).fetchone()
                try:
                    current = self._result(existing, cached=True) if existing else None
                except SourceCacheError:
                    if not replace:
                        raise
                    current = None  # A refresh replaces an unreadable entry.
                if current is not None:
                    # A capture fetched from its own canonical URL is replaced only by a
                    # refresh of that URL, never by a page that names it canonical.
                    protected = stored != requested and (
                        get_provider(provider).fetched_identity(current.source) == stored
                    )
                    if protected or (
                        (reuse is not None or not replace)
                        and (reuse is None or reuse(current.source, stored))
                    ):
                        return current
                source_id = existing[0] if existing is not None else new_id
                if existing is not None:
                    # Cascades to every row derived from the old capture.
                    connection.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
                connection.execute(
                    "INSERT INTO sources (source_id, capture_id, provider, native_id, "
                    "exclude_sponsors, payload, checksum, content_sha256) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (source_id, capture_id, *key, payload, checksum, content),
                )
                if original is not None:
                    connection.execute(
                        "INSERT INTO original_payloads VALUES (?, ?, ?, ?)",
                        (capture_id, original.format.value, original.data, original.sha256),
                    )
        except sqlite3.Error as error:
            raise SourceCacheError(f"Cannot write source cache {self.path}: {error}") from error
        return CachedSource(source_id, result.source, cached=False)


def _serves(provider, source: Source, requested: str, stored: str) -> bool:
    """A capture answers a request when it was fetched from the requested URL or from
    the URL it is stored under; a page naming another page canonical never stands in
    for that page."""
    return provider.fetched_identity(source) in {requested, stored}


def _content_hash(source: Source) -> str:
    """The SHA-256 of a capture's text atoms, to report identical content across URLs."""
    digest = hashlib.sha256()
    for segment in source.transcript.segments:
        digest.update(segment.text.encode())
        digest.update(b"\0")
    return digest.hexdigest()
