"""Format-aware parsing of one selected caption response into ordered source atoms."""

import html
import json
import re
from math import isfinite

from obsidian_ingest.extractor.acquisition import CaptionError, CaptionFormat
from obsidian_ingest.extractor.transcript import TranscriptSegment

_VTT_TIME = r"(?:(\d+):)?([0-5]\d):([0-5]\d)\.(\d{3})"
_VTT_TIMING = re.compile(rf"^{_VTT_TIME}[ \t]+-->[ \t]+{_VTT_TIME}(?:[ \t].*)?$")
_VTT_TAG = re.compile(r"<[^>]*>")
_BLANK_LINES = re.compile(r"\n[ \t]*\n")


def _milliseconds(event: dict, field: str, index: int) -> float:
    value = event[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptionError(f"JSON3 event {index} has non-numeric {field}")
    if not isfinite(value) or value < 0:
        raise CaptionError(f"JSON3 event {index} has invalid {field}")
    return value / 1000


def parse_json3(captions: dict) -> tuple[TranscriptSegment, ...]:
    """Keep each timed text event as one atom; untimed and textless events are not speech text."""
    if not isinstance(captions, dict):
        raise CaptionError("JSON3 caption response must be an object")
    events = captions.get("events", [])
    if not isinstance(events, list):
        raise CaptionError("JSON3 caption events must be a list")
    segments = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise CaptionError(f"JSON3 event {index} must be an object")
        segs = event.get("segs")
        if not segs or "tStartMs" not in event or "dDurationMs" not in event:
            continue
        if not isinstance(segs, list) or not all(isinstance(seg, dict) for seg in segs):
            raise CaptionError(f"JSON3 event {index} has malformed text segments")
        parts = [seg.get("utf8", "") for seg in segs]
        if not all(isinstance(part, str) for part in parts):
            raise CaptionError(f"JSON3 event {index} has non-text segments")
        text = "".join(parts).strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                text=text,
                start=_milliseconds(event, "tStartMs", index),
                duration=_milliseconds(event, "dDurationMs", index),
            )
        )
    return tuple(segments)


def _vtt_seconds(hours: str | None, minutes: str, seconds: str, millis: str) -> int:
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def _is_vtt_metadata_block(first_line: str) -> bool:
    return "-->" not in first_line and any(
        first_line == name or first_line.startswith((f"{name} ", f"{name}\t"))
        for name in ("NOTE", "STYLE", "REGION")
    )


def parse_vtt(text: str) -> tuple[TranscriptSegment, ...]:
    """Keep each WebVTT cue as one atom, in file order, with multiline text joined by newlines.

    Markup tags are removed and character references decoded; wording, overlaps, and
    repeated rolling-caption text are preserved as delivered.
    """
    text = text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocks = _BLANK_LINES.split(text.strip())
    header, *header_lines = blocks[0].split("\n")
    if header != "WEBVTT" and not header.startswith(("WEBVTT ", "WEBVTT\t")):
        raise CaptionError("Malformed WebVTT caption response: missing WEBVTT header")
    # Header metadata (e.g. "Kind: captions") may follow, but a cue needs a blank line
    # before it; discarding one with the header would silently drop caption text.
    if any("-->" in line for line in header_lines):
        raise CaptionError(
            "Malformed WebVTT caption response: cue timing inside the header block "
            "(missing blank line after the WEBVTT header)"
        )
    segments = []
    previous_start = -1
    for block in blocks[1:]:
        lines = block.split("\n")
        if _is_vtt_metadata_block(lines[0]):
            continue
        timing_index = 0 if "-->" in lines[0] else 1
        match = _VTT_TIMING.match(lines[timing_index]) if timing_index < len(lines) else None
        if match is None:
            raise CaptionError(
                f"Malformed WebVTT cue timing: {lines[min(timing_index, len(lines) - 1)]!r}"
            )
        start = _vtt_seconds(*match.groups()[:4])
        end = _vtt_seconds(*match.groups()[4:])
        if end < start:
            raise CaptionError(f"WebVTT cue ends before it starts: {lines[timing_index]!r}")
        if start < previous_start:
            raise CaptionError("WebVTT cues are not in start-time order")
        previous_start = start
        cue_text = "\n".join(
            html.unescape(_VTT_TAG.sub("", line)) for line in lines[timing_index + 1 :]
        )
        cue_text = cue_text.strip()
        if cue_text:
            segments.append(TranscriptSegment(cue_text, start / 1000, (end - start) / 1000))
    return tuple(segments)


def parse_captions(data: bytes, caption_format: CaptionFormat) -> tuple[TranscriptSegment, ...]:
    """Parse by declared format; unusable or textless responses fail instead of looking silent."""
    if not data.strip():
        raise CaptionError(f"Empty {caption_format.value} caption response")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CaptionError(f"{caption_format.value} caption response is not UTF-8") from error
    if caption_format == CaptionFormat.JSON3:
        try:
            captions = json.loads(text)
        except json.JSONDecodeError as error:
            raise CaptionError(f"Malformed JSON3 caption response: {error}") from error
        segments = parse_json3(captions)
    elif caption_format == CaptionFormat.VTT:
        segments = parse_vtt(text)
    else:
        raise CaptionError(f"Unsupported caption format: {caption_format}")
    if not segments:
        raise CaptionError(
            f"{caption_format.value} caption response contained no timed caption text"
        )
    return segments
