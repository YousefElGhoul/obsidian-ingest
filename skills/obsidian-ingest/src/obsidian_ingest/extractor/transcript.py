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
MAX_LISTED_TIMESTAMPS = 12


def distinct_moments(timestamps: tuple[float, ...]) -> tuple[float, ...]:
    """List distinct moments: weak models often cite every caption line of one passage.

    Keeps the earliest of timestamps closer than TIMESTAMP_GAP_SECONDS, in time order.
    Callers list at most MAX_LISTED_TIMESTAMPS and count the rest (see `listed`).
    """
    kept: list[float] = []
    for timestamp in sorted(timestamps):
        if not kept or timestamp - kept[-1] >= TIMESTAMP_GAP_SECONDS:
            kept.append(timestamp)
    return tuple(kept)


def listed(items: list[str], limit: int, separator: str) -> str:
    """Join the first ``limit`` items and say how many more were cited, never dropping
    them silently."""
    text = separator.join(items[:limit])
    hidden = len(items) - limit
    return f"{text}{separator}and {hidden} more" if hidden > 0 else text


def format_timestamp(seconds: float) -> str:
    """Truncate fractional seconds and render MM:SS, adding hours when nonzero."""
    total_seconds = int(seconds)

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    return f"{minutes:02}:{seconds:02}"
