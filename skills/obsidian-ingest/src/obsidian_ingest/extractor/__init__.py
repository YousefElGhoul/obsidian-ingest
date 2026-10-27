from enum import StrEnum
from urllib.parse import urlparse


class Provider(StrEnum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


class ContentCategory(StrEnum):
    CLIP = "clip"
    SHORT_FORM = "short_form"
    LONG_FORM = "long_form"
    EXTENDED = "extended"


def detect_provider(url: str) -> Provider:
    host = (urlparse(url).hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return Provider.YOUTUBE
    if host in {"instagram.com", "www.instagram.com"}:
        return Provider.INSTAGRAM
    if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com"}:
        return Provider.TIKTOK
    raise ValueError(f"Unsupported provider for URL: {url}")
