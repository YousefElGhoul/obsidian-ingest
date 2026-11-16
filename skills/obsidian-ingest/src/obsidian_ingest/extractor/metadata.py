"""Define normalized acquisition metadata and exclusion provenance for source navigation."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from obsidian_ingest.extractor import ContentCategory, Provider, detect_provider


class NativeFormat(StrEnum):
    """Provider-native presentation format, independent of duration-based categories."""

    STANDARD = "standard"
    VERTICAL_SHORT = "vertical_short"


class ExclusionStatus(StrEnum):
    """Distinguish a completed lookup, an intentional opt-out, and unavailable filtering."""

    COMPLETE = "complete"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExcludedRange:
    """Attribute a half-open exclusion interval in source seconds to its lookup provider."""

    start: float
    end: float
    reason: str
    provenance: str


@dataclass(frozen=True, slots=True)
class ExclusionLookup:
    """Retain requested categories and lookup outcome, including explicit failure details."""

    ranges: tuple[ExcludedRange, ...]
    status: ExclusionStatus
    provenance: str
    categories: tuple[str, ...]
    retrieved_at: datetime | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class Chapter:
    """Preserve a creator's titled interval in source seconds without semantic repartitioning."""

    title: str
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class YouTubeMetadata:
    """Keep normalized YouTube-only context separate from common source fields."""

    channel_handle: str | None
    playlist_id: str | None
    playlist_title: str | None
    playlist_index: int | None
    is_short: bool
    live_status: str | None
    was_live: bool | None


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Carry source identity, attribution, and navigation context into curation."""

    provider: Provider
    source_id: str

    original_url: str
    canonical_url: str

    title: str | None
    description: str | None

    creator_name: str | None
    creator_id: str | None
    creator_url: str | None

    duration_seconds: float | None
    published_at: datetime | None

    language: str | None

    tags: tuple[str, ...]
    chapters: tuple[Chapter, ...]
    native_format: NativeFormat

    provider_details: YouTubeMetadata | None = None


def categorize_content(metadata: SourceMetadata) -> ContentCategory | None:
    """Derive a category without guessing a missing duration."""
    if metadata.native_format == NativeFormat.VERTICAL_SHORT:
        return ContentCategory.CLIP
    duration = metadata.duration_seconds
    if duration is None:
        return None
    # Navigation bands: under eight minutes, eight through fifty, and over fifty.
    if duration < 480:
        return ContentCategory.SHORT_FORM
    if duration <= 3000:
        return ContentCategory.LONG_FORM
    return ContentCategory.EXTENDED


def fetch_metadata(url: str) -> SourceMetadata:
    """Dispatch metadata acquisition, raising ValueError for providers without an extractor."""
    provider = detect_provider(url)
    if provider == Provider.YOUTUBE:
        from obsidian_ingest.extractor.providers.youtube import get_youtube_metadata

        return get_youtube_metadata(url)
    raise ValueError(f"Metadata extraction is not supported for provider: {provider}")
