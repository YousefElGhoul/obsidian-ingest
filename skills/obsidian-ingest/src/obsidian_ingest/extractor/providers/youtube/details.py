"""YouTube-only metadata kept beside, not inside, the shared source model."""

from dataclasses import dataclass


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
