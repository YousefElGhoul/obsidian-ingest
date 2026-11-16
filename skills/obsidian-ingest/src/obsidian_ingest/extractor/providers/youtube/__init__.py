"""Acquire YouTube evidence and normalize provider fields before application curation."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp
import yt_dlp.version

from obsidian_ingest.extractor import Provider
from obsidian_ingest.extractor.acquisition import (
    MAX_CAPTION_BYTES,
    Acquisition,
    CaptionError,
    CaptionFormat,
    OriginalCaption,
    describe_original,
)
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExclusionLookup,
    ExclusionStatus,
    NativeFormat,
    SourceMetadata,
    YouTubeMetadata,
)
from obsidian_ingest.extractor.providers.youtube.captions import (
    parse_captions,
    parse_json3,
    parse_vtt,
)
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment

__all__ = (
    "YouTubeContent",
    "choose_caption_format",
    "choose_caption_track",
    "extract_video_id",
    "get_youtube_content",
    "get_youtube_metadata",
    "get_youtube_subtitles",
    "normalize_youtube_metadata",
    "parse_captions",
    "parse_json3",
    "parse_vtt",
)


# Caption selection and source identity


def _select_caption_track(info: dict) -> tuple[str, bool, list[dict]]:
    """Return the yt-dlp track key, generated flag, and offered formats."""
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
                return original_english[0], True, tracks[original_english[0]]

        if "en" in tracks:
            return "en", is_generated, tracks["en"]

        english_variants = sorted(lang for lang in tracks if lang.startswith("en-"))
        if english_variants:
            return english_variants[0], is_generated, tracks[english_variants[0]]

    raise ValueError("No English captions available")


def choose_caption_track(info: dict) -> tuple[str, bool, list[dict]]:
    """Select English captions, returning language, generated flag, and offered formats."""
    track_key, is_generated, formats = _select_caption_track(info)
    return track_key.removesuffix("-orig"), is_generated, formats


def choose_caption_format(formats: list[dict]) -> dict:
    """Prefer JSON3 over VTT; raise CaptionError when neither format is offered."""
    for preferred in CaptionFormat:
        for caption_format in formats:
            if caption_format.get("ext") == preferred.value:
                return caption_format

    offered = ", ".join(sorted({str(item.get("ext")) for item in formats})) or "none"
    raise CaptionError(
        f"No supported caption format available (supported: json3, vtt; offered: {offered})"
    )


def _translation_status(track_key: str, caption_format: dict) -> bool | None:
    """yt-dlp marks machine translations with tlang and native ASR with -orig; else unknown."""
    query = parse_qs(urlparse(str(caption_format.get("url") or "")).query)
    if "tlang" in query:
        return True
    if track_key.endswith("-orig"):
        return False
    return None


def extract_video_id(url: str) -> str:
    """Read a video identity from supported YouTube URL forms, or raise ValueError."""
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


# Acquisition and provider normalization


@dataclass(frozen=True, slots=True)
class _Captions:
    """Keep parsed source atoms paired with the exact response that produced them."""

    language: str
    is_generated: bool
    segments: tuple[TranscriptSegment, ...]
    original: OriginalCaption
    is_translated: bool | None


def get_youtube_subtitles(url: str) -> tuple[str, str, bool, tuple[TranscriptSegment, ...]]:
    """Fetch English captions as video ID, language, generated flag, and source atoms."""
    video_id = extract_video_id(url)

    ydl_opts = {"quiet": True, "no_warnings": True}

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        captions = _read_captions(ydl, info)

    return video_id, captions.language, captions.is_generated, captions.segments


def _read_response(response: Any) -> bytes:
    """Read at most one byte beyond the cap so oversized responses fail explicitly."""
    data = response.read(MAX_CAPTION_BYTES + 1)
    if not isinstance(data, bytes):
        raise CaptionError("Caption response did not return bytes")
    if len(data) > MAX_CAPTION_BYTES:
        raise CaptionError(f"Caption response exceeds {MAX_CAPTION_BYTES} bytes")
    return data


def _read_captions(ydl: yt_dlp.YoutubeDL, info: dict) -> _Captions:
    """Fetch one selected track, retaining its original bytes alongside normalized atoms."""
    track_key, is_generated, formats = _select_caption_track(info)
    caption = choose_caption_format(formats)
    if not isinstance(caption.get("url"), str) or not caption["url"]:
        raise CaptionError("Selected caption format has no URL")
    caption_format = CaptionFormat(caption["ext"])
    data = _read_response(ydl.urlopen(caption["url"]))
    return _Captions(
        language=track_key.removesuffix("-orig"),
        is_generated=is_generated,
        segments=parse_captions(data, caption_format),
        original=OriginalCaption(caption_format, data),
        is_translated=_translation_status(track_key, caption),
    )


def _yt_dlp_version() -> str | None:
    version = getattr(yt_dlp.version, "__version__", None)
    return version if isinstance(version, str) and version else None


def _extract_metadata(ydl: yt_dlp.YoutubeDL, url: str) -> tuple[dict, SourceMetadata]:
    """Reject playlist results before translating a single video's metadata."""
    info = ydl.extract_info(url, download=False)
    if not info or info.get("_type") in {"playlist", "multi_video"}:
        raise ValueError("Expected metadata for a single YouTube video")
    return info, normalize_youtube_metadata(info, url)


@dataclass(frozen=True, slots=True)
class YouTubeContent:
    """Carry one extraction snapshot and its best-effort exclusions into source assembly."""

    metadata: SourceMetadata
    transcript: Transcript
    exclusions: ExclusionLookup
    acquisition: Acquisition
    original_caption: OriginalCaption


def get_youtube_content(url: str, categories: tuple[str, ...] = ("sponsor",)) -> YouTubeContent:
    """Build metadata, captions, and best-effort exclusions from one extraction snapshot."""
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info, metadata = _extract_metadata(ydl, url)
        try:
            from obsidian_ingest.extractor.providers.youtube.sponsorblock import fetch_exclusions

            exclusions = fetch_exclusions(ydl, info, categories)
        except Exception as error:  # noqa: BLE001 - filtering is optional; captions still proceed.
            exclusions = ExclusionLookup(
                ranges=(),
                status=ExclusionStatus.FAILED,
                provenance="SponsorBlock",
                categories=categories,
                error_message=str(error) or type(error).__name__,
            )
        captions = _read_captions(ydl, info)
    acquired_at = datetime.now(UTC)
    transcript = Transcript(
        metadata.source_id,
        captions.language,
        captions.language,
        captions.is_generated,
        captions.segments,
    )
    acquisition = Acquisition(
        acquired_at=acquired_at,
        extractor="yt-dlp",
        extractor_version=_yt_dlp_version(),
        caption=describe_original(captions.original, captions.is_translated),
    )
    return YouTubeContent(metadata, transcript, exclusions, acquisition, captions.original)


def get_youtube_metadata(url: str) -> SourceMetadata:
    """Acquire normalized metadata for one video without downloading its media."""
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
