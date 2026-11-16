"""Provider-neutral caption acquisition provenance and the retained original payload."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

# Bounded read of one caption response; this limits bytes accepted, not network transfer.
MAX_CAPTION_BYTES = 16 * 1024 * 1024


class CaptionFormat(StrEnum):
    """Supported caption response formats, in acquisition preference order."""

    JSON3 = "json3"
    VTT = "vtt"


class CaptionError(ValueError):
    """An empty, malformed, oversized, or unsupported caption acquisition."""


@dataclass(frozen=True, slots=True)
class CaptionTrack:
    """The selected caption response; ``is_translated`` is None when the provider is silent."""

    format: CaptionFormat
    is_translated: bool | None
    payload_bytes: int
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class OriginalCaption:
    """The selected caption response exactly as received, before parsing."""

    format: CaptionFormat
    data: bytes

    @property
    def sha256(self) -> str:
        """Identify the exact retained bytes independently of their parsed transcript."""
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True, slots=True)
class Acquisition:
    """When and with which extractor one capture was made; unknown version stays None."""

    acquired_at: datetime
    extractor: str
    extractor_version: str | None
    caption: CaptionTrack


def describe_original(original: OriginalCaption, is_translated: bool | None) -> CaptionTrack:
    """Build compact provenance for original bytes without embedding them in the snapshot."""
    return CaptionTrack(original.format, is_translated, len(original.data), original.sha256)
