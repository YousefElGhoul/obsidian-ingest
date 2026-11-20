"""Source atoms in source order, plus timed-source helpers shared by providers."""

from dataclasses import dataclass

from obsidian_ingest.extractor import SourceKind


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One source atom, unchanged from the source.

    Timed atoms (caption events or cues) have start and duration in seconds. Text atoms
    (an article's paragraphs, list items, code blocks...) have no times; they carry
    their heading path as ``section`` and their block ``kind`` instead.
    """

    text: str
    start: float | None = None
    duration: float | None = None
    section: str | None = None
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class Transcript:
    """Keep source-ordered atoms and their language/origin together."""

    source_id: str
    language: str
    language_code: str
    is_generated: bool
    segments: tuple[TranscriptSegment, ...]
    kind: SourceKind = SourceKind.TIMED


TIMESTAMP_GAP_SECONDS = 30
MAX_LISTED_TIMESTAMPS = 8


def distinct_moments(timestamps: tuple[float, ...]) -> tuple[float, ...]:
    """List distinct moments: weak models often cite every caption line of one passage.

    Keeps the earliest of timestamps closer than TIMESTAMP_GAP_SECONDS, in time order,
    up to MAX_LISTED_TIMESTAMPS.
    """
    kept: list[float] = []
    for timestamp in sorted(timestamps):
        if not kept or timestamp - kept[-1] >= TIMESTAMP_GAP_SECONDS:
            kept.append(timestamp)
    return tuple(kept[:MAX_LISTED_TIMESTAMPS])


def format_timestamp(seconds: float) -> str:
    """Truncate fractional seconds and render MM:SS, adding hours when nonzero."""
    total_seconds = int(seconds)

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    return f"{minutes:02}:{seconds:02}"
