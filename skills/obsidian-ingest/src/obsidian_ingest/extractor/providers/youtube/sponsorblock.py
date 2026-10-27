import json
from datetime import UTC, datetime
from math import isfinite

from yt_dlp import YoutubeDL
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.postprocessor.sponsorblock import SponsorBlockPP

from obsidian_ingest.extractor.metadata import ExcludedRange, ExclusionLookup, ExclusionStatus

DEFAULT_DISCARD_CATEGORIES = ("sponsor",)
_PROVENANCE = "SponsorBlock"


class _StrictSponsorBlockPP(SponsorBlockPP):
    def report_warning(self, text: str, *args, **kwargs) -> None:
        # There is no partial status: warnings must not become COMPLETE results.
        raise RuntimeError(f"SponsorBlock lookup failed: {text}")

    def _download_json(self, url: str, *, expected_http_errors=(404,)) -> list[dict]:
        # Use yt-dlp's existing transport, distinguishing no matches from failures.
        try:
            response = self._downloader.urlopen(Request(url))
        except HTTPError as error:
            if error.status == 404:
                return []
            raise
        result = json.loads(
            response.read().decode(response.headers.get_param("charset") or "utf-8")
        )
        if not isinstance(result, list):
            raise TypeError("SponsorBlock API response must be a list")
        return result

    def _get_sponsor_segments(self, video_id: str, service: str) -> list[dict]:
        segments = super()._get_sponsor_segments(video_id, service)
        # Validate before yt-dlp snaps/clamps endpoints, which can hide invalid input.
        for segment in segments:
            if (
                segment.get("category") not in self._categories
                or segment.get("actionType") != "skip"
            ):
                continue
            interval = segment.get("segment")
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                raise ValueError("SponsorBlock segment must contain two timestamps")
            start = _seconds(interval[0], "start_time")
            end = _seconds(interval[1], "end_time")
            if start >= end:
                raise ValueError("SponsorBlock range must satisfy 0 <= start_time < end_time")
        return segments


def _validate_categories(categories: tuple[str, ...]) -> None:
    skip_categories = (
        SponsorBlockPP.CATEGORIES.keys() - SponsorBlockPP.NON_SKIPPABLE_CATEGORIES.keys()
    )
    for category in categories:
        if category not in skip_categories:
            raise ValueError(f"Invalid SponsorBlock skip category: {category!r}")


def _seconds(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"SponsorBlock {field} must be a finite nonnegative number")
    try:
        seconds = float(value)
    except OverflowError as error:
        raise ValueError(f"SponsorBlock {field} must be finite") from error
    if not isfinite(seconds) or seconds < 0:
        raise ValueError(f"SponsorBlock {field} must be a finite nonnegative number")
    return seconds


def normalize_exclusions(
    chapters: list[dict], categories: tuple[str, ...] = DEFAULT_DISCARD_CATEGORIES
) -> tuple[ExcludedRange, ...]:
    """Keep configured skip ranges in source order, without merging overlaps."""
    _validate_categories(categories)
    ranges = []
    for chapter in chapters:
        category = chapter.get("category")
        if category not in categories or chapter.get("type") != "skip":
            continue
        start = _seconds(chapter.get("start_time"), "start_time")
        end = _seconds(chapter.get("end_time"), "end_time")
        if start >= end:
            raise ValueError("SponsorBlock range must satisfy 0 <= start_time < end_time")
        ranges.append(ExcludedRange(start, end, category, _PROVENANCE))
    return tuple(ranges)


def fetch_exclusions(
    ydl: YoutubeDL, info: dict, categories: tuple[str, ...] = DEFAULT_DISCARD_CATEGORIES
) -> ExclusionLookup:
    """Inspect extracted video info with the existing downloader; never remove media.

    HTTP 404 means no matching ranges. Other errors and duration-mismatch warnings
    fail explicitly, rather than returning a misleading complete, empty lookup.
    """
    _validate_categories(categories)
    if not categories:
        return ExclusionLookup((), ExclusionStatus.DISABLED, _PROVENANCE, categories)

    duration = _seconds(info.get("duration"), "duration")
    if duration == 0:
        raise ValueError("SponsorBlock duration must be positive")
    if info.get("extractor_key") not in SponsorBlockPP.EXTRACTORS:
        raise ValueError("SponsorBlock requires supported YouTube extractor_key")
    if not isinstance(info.get("id"), str) or not info["id"]:
        raise ValueError("SponsorBlock requires a nonempty video id")

    lookup_info = info.copy()
    lookup_info.pop("sponsorblock_chapters", None)
    try:
        _, result = _StrictSponsorBlockPP(ydl, categories=categories).run(lookup_info)
    except Exception as error:
        raise RuntimeError(f"SponsorBlock lookup failed: {error}") from error
    chapters = result.get("sponsorblock_chapters")
    if chapters is None:
        raise RuntimeError("SponsorBlock lookup failed: missing chapter results")
    if not isinstance(chapters, list):
        raise TypeError("SponsorBlock lookup failed: chapter results must be a list")
    return ExclusionLookup(
        normalize_exclusions(chapters, categories),
        ExclusionStatus.COMPLETE,
        _PROVENANCE,
        categories,
        datetime.now(UTC),
    )
