"""The YouTube implementation of the source provider protocol."""

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

from obsidian_ingest.extractor import Provider
from obsidian_ingest.extractor.metadata import SourceMetadata
from obsidian_ingest.extractor.providers import (
    Citation,
    ProviderContent,
    ProviderDefaults,
    SourceKind,
)
from obsidian_ingest.extractor.providers.youtube import extract_video_id, get_youtube_content
from obsidian_ingest.extractor.providers.youtube.details import YouTubeMetadata
from obsidian_ingest.extractor.providers.youtube.thumbnail import fetch_youtube_thumbnail
from obsidian_ingest.extractor.transcript import (
    TranscriptSegment,
    distinct_moments,
    format_timestamp,
)

_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
_DETAIL_TYPES: dict[str, tuple[type, ...]] = {
    "channel_handle": (str, type(None)),
    "playlist_id": (str, type(None)),
    "playlist_title": (str, type(None)),
    "playlist_index": (int, type(None)),
    "is_short": (bool,),
    "live_status": (str, type(None)),
    "was_live": (bool, type(None)),
}


@dataclass(frozen=True, slots=True)
class YouTubeProvider(ProviderDefaults):
    """Caption-based acquisition with SponsorBlock exclusions and timestamp citations."""

    name: Provider = Provider.YOUTUBE
    kind: SourceKind = SourceKind.TIMED
    supported: bool = True

    def matches_url(self, url: str) -> bool:
        return (urlparse(url).hostname or "").lower() in _HOSTS

    def identity(self, url: str) -> str:
        return extract_video_id(url)

    def exclusion_categories(self, exclude_sponsors: bool) -> tuple[str, ...]:
        return ("sponsor",) if exclude_sponsors else ()

    def acquire(self, url: str, *, exclude_sponsors: bool) -> ProviderContent:
        content = get_youtube_content(url, self.exclusion_categories(exclude_sponsors))
        return ProviderContent(
            content.metadata,
            content.transcript,
            content.exclusions,
            content.acquisition,
            content.original_caption,
        )

    def encode_details(self, details: object | None) -> dict[str, Any] | None:
        if details is None:
            return None
        if not isinstance(details, YouTubeMetadata):
            raise TypeError("unexpected provider details for YouTube")
        return asdict(details)

    def decode_details(self, data: dict[str, Any] | None) -> object | None:
        if data is None:
            return None
        if not isinstance(data, dict) or set(data) != set(_DETAIL_TYPES):
            raise ValueError("malformed YouTube details")
        for key, types in _DETAIL_TYPES.items():
            value = data[key]
            # bool is an int subclass; an index must not be a boolean.
            if not isinstance(value, types) or (
                key == "playlist_index" and isinstance(value, bool)
            ):
                raise ValueError(f"malformed YouTube detail: {key}")
        return YouTubeMetadata(**data)

    def citation(
        self, metadata: SourceMetadata, segments: tuple[TranscriptSegment, ...]
    ) -> Citation | None:
        moments = distinct_moments(tuple(segment.start for segment in segments))
        if not moments:
            return None
        links = []
        for moment in moments:
            seconds = int(moment)
            url = "https://www.youtube.com/watch?" + urlencode(
                {"v": metadata.source_id, "t": f"{seconds}s"}
            )
            links.append(f"[{format_timestamp(seconds)}]({url})")
        return Citation("Relevant timestamps", ", ".join(links))

    def attribution(self, metadata: SourceMetadata) -> tuple[tuple[str, str], ...]:
        return (("Creator", metadata.creator_name),) if metadata.creator_name else ()

    def thumbnail(self, metadata: SourceMetadata) -> tuple[bytes, str]:
        return fetch_youtube_thumbnail(metadata.source_id)


YOUTUBE = YouTubeProvider()
