from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from obsidian_ingest.extractor import ContentCategory, Provider, detect_provider


class NativeFormat(StrEnum):
    STANDARD = "standard"
    VERTICAL_SHORT = "vertical_short"


class ExclusionStatus(StrEnum):
    COMPLETE = "complete"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ExcludedRange:
    start: float
    end: float
    reason: str
    provenance: str


@dataclass(frozen=True, slots=True)
class ExclusionLookup:
    ranges: tuple[ExcludedRange, ...]
    status: ExclusionStatus
    provenance: str
    categories: tuple[str, ...]
    retrieved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Chapter:
    title: str
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class YouTubeMetadata:
    channel_handle: str | None
    playlist_id: str | None
    playlist_title: str | None
    playlist_index: int | None
    is_short: bool
    live_status: str | None
    was_live: bool | None


@dataclass(frozen=True, slots=True)
class SourceMetadata:
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
    if duration < 480:
        return ContentCategory.SHORT_FORM
    if duration <= 3000:
        return ContentCategory.LONG_FORM
    return ContentCategory.EXTENDED


def fetch_metadata(url: str) -> SourceMetadata:
    provider = detect_provider(url)
    if provider == Provider.YOUTUBE:
        from obsidian_ingest.extractor.providers.youtube import get_youtube_metadata

        return get_youtube_metadata(url)
    raise ValueError(f"Metadata extraction is not supported for provider: {provider}")
