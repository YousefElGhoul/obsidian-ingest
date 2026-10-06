import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp

from obsidian_ingest.extractor import Provider
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExclusionLookup,
    NativeFormat,
    SourceMetadata,
    YouTubeMetadata,
)
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment


def choose_caption_track(info: dict) -> tuple[str, bool, list[dict]]:
    for tracks, is_generated in (
        (info.get("subtitles") or {}, False),
        (info.get("automatic_captions") or {}, True),
    ):
        if is_generated:
            # Bare "en" can be a translation; yt-dlp marks native ASR tracks with -orig.
            original_english = (
                ["en-orig"]
                if "en-orig" in tracks
                else sorted(
                    lang for lang in tracks if lang.startswith("en-") and lang.endswith("-orig")
                )
            )
            if original_english:
                track_key = original_english[0]
                return track_key.removesuffix("-orig"), True, tracks[track_key]

        if "en" in tracks:
            return "en", is_generated, tracks["en"]

        english_variants = sorted(lang for lang in tracks if lang.startswith("en-"))
        if english_variants:
            language = english_variants[0]
            return language, is_generated, tracks[language]

    raise ValueError("No English captions available")


def choose_caption_format(formats: list[dict]) -> dict:
    for preferred in ("json3", "vtt"):
        for caption_format in formats:
            if caption_format.get("ext") == preferred:
                return caption_format

    raise ValueError("No supported caption format available")


def extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/")

    elif host == "youtube.com" or host.endswith(".youtube.com"):
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            parts = parsed.path.strip("/").split("/")
            video_id = parts[1] if len(parts) >= 2 else ""
        else:
            video_id = ""
    else:
        video_id = ""

    if not video_id:
        raise ValueError(f"Could not extract a YouTube video ID from: {url}")

    return str(video_id)


def parse_json3(captions: dict) -> tuple[TranscriptSegment, ...]:
    segments = []
    for event in captions.get("events", []):
        segs = event.get("segs")
        if not segs or "tStartMs" not in event or "dDurationMs" not in event:
            continue

        text = "".join(segment.get("utf8", "") for segment in segs).strip()
        if not text:
            continue

        segments.append(
            TranscriptSegment(
                text=text,
                start=event["tStartMs"] / 1000,
                duration=event["dDurationMs"] / 1000,
            )
        )
    return tuple(segments)


def get_youtube_subtitles(url: str) -> tuple[str, str, bool, tuple[TranscriptSegment, ...]]:

    video_id = extract_video_id(url)

    ydl_opts = {"quiet": True, "no_warnings": True}

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        language, is_generated, segments = _read_captions(ydl, info)

    return video_id, language, is_generated, segments


def _read_captions(
    ydl: yt_dlp.YoutubeDL, info: dict
) -> tuple[str, bool, tuple[TranscriptSegment, ...]]:
    language, is_generated, formats = choose_caption_track(info)
    caption = choose_caption_format(formats)
    response = ydl.urlopen(caption["url"])
    captions = json.loads(response.read().decode("utf-8"))
    return language, is_generated, parse_json3(captions)


def _extract_metadata(ydl: yt_dlp.YoutubeDL, url: str) -> tuple[dict, SourceMetadata]:
    info = ydl.extract_info(url, download=False)
    if not info or info.get("_type") in {"playlist", "multi_video"}:
        raise ValueError("Expected metadata for a single YouTube video")
    return info, normalize_youtube_metadata(info, url)


def get_youtube_content(url: str) -> tuple[SourceMetadata, Transcript]:
    """Build normalized metadata and captions from one extraction, without exclusions."""
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info, metadata = _extract_metadata(ydl, url)
        language, generated, segments = _read_captions(ydl, info)
    return metadata, Transcript(metadata.source_id, language, language, generated, segments)


def get_youtube_source(url: str) -> tuple[SourceMetadata, Transcript, ExclusionLookup]:
    """Fetch one source snapshot for inspection without changing the raw transcript."""
    from obsidian_ingest.extractor.providers.youtube.sponsorblock import fetch_exclusions

    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info, metadata = _extract_metadata(ydl, url)
        exclusions = fetch_exclusions(ydl, info)
        language, generated, segments = _read_captions(ydl, info)
    transcript = Transcript(metadata.source_id, language, language, generated, segments)
    return metadata, transcript, exclusions


def get_youtube_metadata(url: str) -> SourceMetadata:
    ydl_opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        _, metadata = _extract_metadata(ydl, url)
    return metadata


def normalize_youtube_metadata(info: dict[str, Any], original_url: str) -> SourceMetadata:
    """Translate yt-dlp fields at the provider boundary, preserving the input URL."""
    source_id = info.get("id") or extract_video_id(original_url)
    canonical_url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={source_id}"

    published_at = None
    if info.get("timestamp") is not None:
        published_at = datetime.fromtimestamp(info["timestamp"], tz=UTC)
    elif info.get("upload_date"):
        # Date-only fallback: midnight UTC is not a known publication time.
        published_at = datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=UTC)

    # yt-dlp resolves YouTube's isShortsEligible signal independently of URL syntax.
    media_type = info.get("media_type")
    is_short = media_type == "short"
    if media_type not in {"short", "video", "livestream"}:
        for url in (original_url, info.get("original_url"), canonical_url):
            if not url:
                continue
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if (host == "youtube.com" or host.endswith(".youtube.com")) and parsed.path.startswith(
                "/shorts/"
            ):
                is_short = True
                break

    chapters = tuple(
        Chapter(
            title=chapter["title"],
            start=float(chapter["start_time"]),
            end=float(chapter["end_time"]),
        )
        for chapter in info.get("chapters") or ()
        if all(chapter.get(field) is not None for field in ("title", "start_time", "end_time"))
    )
    handle = info.get("uploader_id")
    duration = info.get("duration")
    return SourceMetadata(
        provider=Provider.YOUTUBE,
        source_id=source_id,
        original_url=original_url,
        canonical_url=canonical_url,
        title=info.get("title"),
        description=info.get("description"),
        creator_name=info.get("channel"),
        creator_id=info.get("channel_id"),
        creator_url=info.get("channel_url"),
        duration_seconds=float(duration) if duration is not None else None,
        published_at=published_at,
        language=info.get("language"),
        tags=tuple(info.get("tags") or ()),
        chapters=chapters,
        native_format=NativeFormat.VERTICAL_SHORT if is_short else NativeFormat.STANDARD,
        provider_details=YouTubeMetadata(
            channel_handle=handle if handle and handle.startswith("@") else None,
            playlist_id=info.get("playlist_id"),
            playlist_title=info.get("playlist_title"),
            playlist_index=info.get("playlist_index"),
            is_short=is_short,
            live_status=info.get("live_status"),
            was_live=info.get("was_live"),
        ),
    )
