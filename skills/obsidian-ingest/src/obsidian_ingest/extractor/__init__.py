"""Identify source providers and shared content categories before acquisition and curation."""

from enum import StrEnum
from urllib.parse import urlparse


class Provider(StrEnum):
    """Recognized URL providers; recognition alone does not imply acquisition support."""

    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class ContentCategory(StrEnum):
    """Coarse format/duration labels for navigation, not semantic classifications."""

    CLIP = "clip"
    SHORT_FORM = "short_form"
    LONG_FORM = "long_form"
    EXTENDED = "extended"


def detect_provider(url: str) -> Provider:
    """Map a recognized URL host to a provider, raising ValueError for unsupported hosts."""
    host = (urlparse(url).hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return Provider.YOUTUBE
    if host in {"instagram.com", "www.instagram.com"}:
        return Provider.INSTAGRAM
    if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com"}:
        return Provider.TIKTOK
    raise ValueError(f"Unsupported provider for URL: {url}")
