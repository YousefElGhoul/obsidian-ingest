from dataclasses import dataclass

from obsidian_ingest.extractor import Provider, detect_provider


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    text: str
    start: float
    duration: float


@dataclass(frozen=True, slots=True)
class Transcript:
    video_id: str
    language: str
    language_code: str
    is_generated: bool
    segments: tuple[TranscriptSegment, ...]


def fetch_transcript(
    url: str,
) -> Transcript:
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
    total_seconds = int(seconds)

    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    return f"{minutes:02}:{seconds:02}"


def print_transcript(url: str) -> None:
    transcript = fetch_transcript(url)

    print(f"Video: {transcript.video_id}")
    print(f"Language: {transcript.language} ({transcript.language_code})")
    print(f"Generated captions: {transcript.is_generated}")
    print(f"Segments: {len(transcript.segments)}")
    print()

    for segment in transcript.segments:
        timestamp = format_timestamp(segment.start)
        print(f"[{timestamp}] {segment.text}")
