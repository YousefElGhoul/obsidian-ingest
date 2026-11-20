"""Caption provenance and timing observations for adapters, without quality judgments.

Timing observations compare caption timestamps with the provider-reported duration.
They are not quality scores, proof of missing speech, or proof of complete coverage:
music, silence, or on-screen text can explain a gap, and provider durations can be wrong.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from obsidian_ingest.extractor import SourceKind
from obsidian_ingest.extractor.metadata import SourceMetadata
from obsidian_ingest.extractor.transcript import Transcript

if TYPE_CHECKING:
    from obsidian_ingest.source import Source

# A stretch of at least this many seconds without any caption atom is reported.
GAP_THRESHOLD_SECONDS = 30.0
# Captions extending past the provider duration by more than this are reported.
OVERRUN_TOLERANCE_SECONDS = 2.0
# Serialized diagnostics list at most this many gaps; gap_count stays exact.
MAX_REPORTED_GAPS = 10

TIMING_NOTE = (
    "Caption timing observations only; not a quality score, proof of missing speech, "
    "or proof of complete coverage."
)


class GapPosition(StrEnum):
    """Locate an uncaptioned interval relative to the original caption sequence."""

    LEADING = "leading"
    INTERNAL = "internal"
    TRAILING = "trailing"


@dataclass(frozen=True, slots=True)
class CaptionGap:
    """Describe one sufficiently long interval without caption coverage, in seconds."""

    start: float
    end: float
    position: GapPosition


@dataclass(frozen=True, slots=True)
class TimingObservations:
    """Keep original-caption timing observations separate from filtering and usefulness."""

    caption_segment_count: int
    first_caption_start: float | None
    last_caption_end: float | None
    provider_duration_seconds: float | None
    gaps: tuple[CaptionGap, ...]
    overrun_seconds: float | None


def observe_timing(metadata: SourceMetadata, transcript: Transcript) -> TimingObservations:
    """Observe caption extent, uncaptioned stretches, and overruns of the original atoms."""
    segments = transcript.segments
    duration = metadata.duration_seconds
    gaps = []
    covered_until = 0.0
    for index, segment in enumerate(segments):
        if segment.start - covered_until >= GAP_THRESHOLD_SECONDS:
            position = GapPosition.LEADING if index == 0 else GapPosition.INTERNAL
            gaps.append(CaptionGap(covered_until, segment.start, position))
        # Rolling captions may overlap; only the furthest covered end advances coverage.
        covered_until = max(covered_until, segment.start + segment.duration)
    if duration is not None and duration - covered_until >= GAP_THRESHOLD_SECONDS:
        gaps.append(CaptionGap(covered_until, duration, GapPosition.TRAILING))
    last_end = covered_until if segments else None
    overrun = None
    if (
        duration is not None
        and last_end is not None
        and last_end - duration > OVERRUN_TOLERANCE_SECONDS
    ):
        overrun = last_end - duration
    return TimingObservations(
        caption_segment_count=len(segments),
        first_caption_start=segments[0].start if segments else None,
        last_caption_end=last_end,
        provider_duration_seconds=duration,
        gaps=tuple(gaps),
        overrun_seconds=overrun,
    )


def _seconds(value: float | None) -> float | None:
    return round(float(value), 3) if value is not None else None


def timing_data(timing: TimingObservations) -> dict[str, Any]:
    """Serialize millisecond-rounded observations with bounded gap detail and an exact count."""
    return {
        "caption_segment_count": timing.caption_segment_count,
        "first_caption_start": _seconds(timing.first_caption_start),
        "last_caption_end": _seconds(timing.last_caption_end),
        "provider_duration_seconds": _seconds(timing.provider_duration_seconds),
        "gap_threshold_seconds": GAP_THRESHOLD_SECONDS,
        "overrun_tolerance_seconds": OVERRUN_TOLERANCE_SECONDS,
        "gap_count": len(timing.gaps),
        "gaps": [
            {"start": _seconds(gap.start), "end": _seconds(gap.end), "position": gap.position.value}
            for gap in timing.gaps[:MAX_REPORTED_GAPS]
        ],
        "gaps_truncated": len(timing.gaps) > MAX_REPORTED_GAPS,
        "overrun_seconds": _seconds(timing.overrun_seconds),
        "note": TIMING_NOTE,
    }


def source_diagnostics(source: "Source", *, cached: bool | None = None) -> dict[str, Any]:
    """Return JSON-serializable acquisition diagnostics; unknown values are None.

    ``cached`` is only known when the caller has just ingested (``CachedSource.cached``);
    pass None when reading an existing handle.
    """
    acquisition = source.acquisition
    track = acquisition.caption if acquisition is not None else None
    transcript = source.transcript
    acquired = {
        "acquired_at": acquisition.acquired_at.isoformat() if acquisition else None,
        "extractor": acquisition.extractor if acquisition else None,
        "extractor_version": acquisition.extractor_version if acquisition else None,
        "reused_from_cache": cached,
    }
    if transcript.kind == SourceKind.TEXT:
        evidence = dict(acquisition.evidence or {}) if acquisition is not None else {}
        return {
            "evidence": {"kind": "text", "language": transcript.language} | evidence,
            "acquisition": acquired,
            "timing": None,
        }
    return {
        "evidence": {
            "kind": "captions",
            "language": transcript.language,
            "origin": "generated" if transcript.is_generated else "manual",
            "format": track.format.value if track is not None else None,
            "translated": track.is_translated if track is not None else None,
            "original_payload_bytes": track.payload_bytes if track is not None else None,
            "original_payload_sha256": track.payload_sha256 if track is not None else None,
        },
        "acquisition": {
            "acquired_at": acquisition.acquired_at.isoformat() if acquisition else None,
            "extractor": acquisition.extractor if acquisition else None,
            "extractor_version": acquisition.extractor_version if acquisition else None,
            "reused_from_cache": cached,
        },
        "timing": timing_data(observe_timing(source.metadata, transcript)),
    }
