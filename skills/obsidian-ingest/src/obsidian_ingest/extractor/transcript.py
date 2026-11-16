"""Define unchanged caption atoms and provider dispatch for acquisition before curation."""

from dataclasses import dataclass

from obsidian_ingest.extractor import Provider, detect_provider


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One source caption event or cue, with start and duration in seconds."""

    text: str
    start: float
    duration: float


@dataclass(frozen=True, slots=True)
class Transcript:
    """Keep source-ordered caption atoms and their selected language/origin together."""

    video_id: str
    language: str
    language_code: str
    is_generated: bool
    segments: tuple[TranscriptSegment, ...]


def fetch_transcript(
    url: str,
) -> Transcript:
    """Acquire a supported source's captions, raising ValueError for unsupported providers."""
    provider = detect_provider(url)
    if provider == Provider.YOUTUBE:
        from obsidian_ingest.extractor.providers.youtube import get_youtube_subtitles

        video_id, language, is_generated, segments = get_youtube_subtitles(url)
    else:
        raise ValueError(f"Unsupported provider for URL: {url}")

    return Transcript(
        video_id=video_id,
        language=language,
        language_code=language,
        is_generated=is_generated,
        segments=segments,
    )


def format_timestamp(seconds: float) -> str:
    """Truncate fractional seconds and render MM:SS, adding hours when nonzero."""
    total_seconds = int(seconds)

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    return f"{minutes:02}:{seconds:02}"


def print_transcript(url: str) -> None:
    """Fetch and print original caption atoms with source identity and start timestamps."""
    transcript = fetch_transcript(url)

    print(f"Video: {transcript.video_id}")
    print(f"Language: {transcript.language} ({transcript.language_code})")
    print(f"Generated captions: {transcript.is_generated}")
    print(f"Segments: {len(transcript.segments)}")
    print()

    for segment in transcript.segments:
        timestamp = format_timestamp(segment.start)
        print(f"[{timestamp}] {segment.text}")
