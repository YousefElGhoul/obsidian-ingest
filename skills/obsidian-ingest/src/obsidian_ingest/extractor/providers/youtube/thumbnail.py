"""Fetch optional YouTube thumbnail evidence on demand, outside caption acquisition and caching."""

from __future__ import annotations

import re

import yt_dlp
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError, RequestError

THUMBNAIL_HOST = "i.ytimg.com"
# 480x360 exists for every video (Shorts included) and is cheap for vision models;
# 320x180 is the fallback.
THUMBNAIL_NAMES = ("hqdefault.jpg", "mqdefault.jpg")
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 10
# Limit the path component to URL-safe identity characters, excluding path/query injection.
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ThumbnailError(Exception):
    """The thumbnail could not be fetched within the bounds."""


def thumbnail_urls(video_id: str) -> tuple[str, ...]:
    """Candidate URLs, built from the video ID only, so no untrusted URL is fetched."""
    if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
        raise ThumbnailError("not a YouTube video ID")
    return tuple(f"https://{THUMBNAIL_HOST}/vi/{video_id}/{name}" for name in THUMBNAIL_NAMES)


def fetch_youtube_thumbnail(video_id: str) -> tuple[bytes, str]:
    """Return the first available thumbnail's bytes and URL, or raise ThumbnailError."""
    urls = thumbnail_urls(video_id)
    options = {"quiet": True, "no_warnings": True, "socket_timeout": TIMEOUT_SECONDS}
    with yt_dlp.YoutubeDL(options) as ydl:
        for url in urls:
            try:
                response = ydl.urlopen(Request(url, extensions={"timeout": TIMEOUT_SECONDS}))
            except HTTPError as error:
                if error.status == 404:
                    continue
                raise ThumbnailError(
                    f"thumbnail request failed with HTTP {error.status}"
                ) from error
            except RequestError as error:
                raise ThumbnailError(f"thumbnail request failed: {error}") from error
            # One extra byte distinguishes an oversized image from an exactly-at-limit image.
            data = response.read(MAX_THUMBNAIL_BYTES + 1)
            if not isinstance(data, bytes):
                raise ThumbnailError("thumbnail response did not return bytes")
            if len(data) > MAX_THUMBNAIL_BYTES:
                raise ThumbnailError(f"thumbnail exceeds {MAX_THUMBNAIL_BYTES} bytes")
            return data, url
    raise ThumbnailError("this video has no thumbnail")
