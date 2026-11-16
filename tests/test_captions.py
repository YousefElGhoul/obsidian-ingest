import json

import pytest

from obsidian_ingest.extractor.acquisition import CaptionError, CaptionFormat
from obsidian_ingest.extractor.providers.youtube.captions import (
    parse_captions,
    parse_json3,
    parse_vtt,
)
from obsidian_ingest.extractor.transcript import TranscriptSegment


def test_parse_json3_keeps_useful_events_atomic() -> None:
    captions = {
        "events": [
            {
                "tStartMs": 1250,
                "dDurationMs": 900,
                "segs": [{"utf8": " Hello "}, {"utf8": "world"}],
            },
            {"tStartMs": 2150, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
            {"tStartMs": 2250, "dDurationMs": 100, "segs": [{"utf8": "next", "tOffsetMs": 50}]},
            {"segs": [{"utf8": "missing time"}]},
            {"tStartMs": 3000, "dDurationMs": 100},
            {"tStartMs": 3100, "dDurationMs": 100, "segs": [{"other": "no text key"}]},
        ]
    }

    assert parse_json3(captions) == (
        TranscriptSegment(text="Hello world", start=1.25, duration=0.9),
        TranscriptSegment(text="next", start=2.25, duration=0.1),
    )


def test_parse_vtt_keeps_cues_multiline_overlapping_and_unicode() -> None:
    text = (
        "﻿WEBVTT Kind: captions\nLanguage: en\n\n"
        "STYLE\n::cue { color: lime }\n\n"
        "NOTE a comment\nthat spans lines\n\n"
        "REGION\nid:fred\n\n"
        "NOTE\ttab-separated comment\n\n"
        "intro\n00:00:01.250 --> 00:00:04.000 align:start position:0%\n"
        "<v Speaker>Café &amp; naïve</v>\n<c.loud>second</c> line  \n\n"
        "00:02.000 --> 00:03.500\nOverlapping 日本語 🙂\n\n"
        "00:00:05.000 --> 00:00:06.000\n\n"
        "1:00:00.000 --> 1:00:00.010\n<00:00:01.000><c> rolling</c>\n\n"
    )
    assert parse_vtt(text) == (
        TranscriptSegment("Café & naïve\nsecond line", 1.25, 2.75),
        TranscriptSegment("Overlapping 日本語 🙂", 2.0, 1.5),
        TranscriptSegment("rolling", 3600.0, 0.01),
    )


@pytest.mark.parametrize(
    "text",
    [
        "WEBVTT\r\n\r\n00:01.000 --> 00:02.000\r\nHi\r\n",
        "WEBVTT\r\r00:01.000 --> 00:02.000\rHi\r",
        "WEBVTT\ttab header\n\n00:01.000 --> 00:02.000\nHi\n",
    ],
    ids=["crlf", "bare-cr", "tab-after-header"],
)
def test_parse_vtt_accepts_line_ending_and_header_variants(text: str) -> None:
    assert parse_vtt(text) == (TranscriptSegment("Hi", 1.0, 1.0),)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("00:01.000 --> 00:02.000\nNo header", "missing WEBVTT header"),
        ("WEBVTTX\n\n00:01.000 --> 00:02.000\nHi", "missing WEBVTT header"),
        ("WEBVTT\n\n00:01 --> 00:02\nBad timing", "Malformed WebVTT cue timing"),
        ("WEBVTT\n\nidentifier only", "Malformed WebVTT cue timing"),
        ("WEBVTT\n\n00:01:61.000 --> 00:02.000\nHi", "Malformed WebVTT cue timing"),
        ("WEBVTT\n\n00:03.000 --> 00:02.000\nBackwards", "ends before it starts"),
        (
            "WEBVTT\n\n00:03.000 --> 00:04.000\nB\n\n00:01.000 --> 00:02.000\nA",
            "not in start-time order",
        ),
        # A cue without a blank line after the header must not be dropped silently.
        (
            "WEBVTT\n00:00.000 --> 00:01.000\nFIRST\n\n00:40.000 --> 00:41.000\nSECOND\n",
            "cue timing inside the header block",
        ),
        (
            "WEBVTT Kind: captions\nLanguage: en\n00:00.000 --> 00:01.000\nFIRST",
            "cue timing inside the header block",
        ),
    ],
)
def test_malformed_vtt_fails_explicitly(text: str, message: str) -> None:
    with pytest.raises(CaptionError, match=message):
        parse_vtt(text)


def _event(**fields) -> bytes:
    event = {"tStartMs": 1, "dDurationMs": 1, "segs": [{"utf8": "x"}]} | fields
    return json.dumps({"events": [event]}).encode()


@pytest.mark.parametrize(
    ("data", "caption_format", "message"),
    [
        (b"", CaptionFormat.JSON3, "Empty json3"),
        (b"  \n", CaptionFormat.VTT, "Empty vtt"),
        (b"\xff\xfe", CaptionFormat.JSON3, "not UTF-8"),
        (b"\xff\xfe", CaptionFormat.VTT, "not UTF-8"),
        (b"{not json", CaptionFormat.JSON3, "Malformed JSON3"),
        (b"[]", CaptionFormat.JSON3, "must be an object"),
        (b'{"events": {}}', CaptionFormat.JSON3, "events must be a list"),
        (b'{"events": [1]}', CaptionFormat.JSON3, "event 0 must be an object"),
        (_event(tStartMs="1"), CaptionFormat.JSON3, "non-numeric tStartMs"),
        (_event(tStartMs=True), CaptionFormat.JSON3, "non-numeric tStartMs"),
        (_event(dDurationMs=None), CaptionFormat.JSON3, "non-numeric dDurationMs"),
        (_event(tStartMs=-1), CaptionFormat.JSON3, "invalid tStartMs"),
        (
            b'{"events": [{"tStartMs": 1, "dDurationMs": Infinity, "segs": [{"utf8": "x"}]}]}',
            CaptionFormat.JSON3,
            "invalid dDurationMs",
        ),
        (_event(segs=[{"utf8": 5}]), CaptionFormat.JSON3, "non-text"),
        (_event(segs="text"), CaptionFormat.JSON3, "malformed text segments"),
        (_event(segs=["text"]), CaptionFormat.JSON3, "malformed text segments"),
        (b'{"events": []}', CaptionFormat.JSON3, "no timed caption text"),
        (b'{"wireMagic": "pb3"}', CaptionFormat.JSON3, "no timed caption text"),
        (b"WEBVTT\n\nNOTE only\n", CaptionFormat.VTT, "no timed caption text"),
    ],
)
def test_unusable_caption_responses_fail_explicitly(
    data: bytes, caption_format: CaptionFormat, message: str
) -> None:
    with pytest.raises(CaptionError, match=message):
        parse_captions(data, caption_format)


def test_parse_captions_dispatches_by_format() -> None:
    json3 = json.dumps(
        {"events": [{"tStartMs": 0, "dDurationMs": 5, "segs": [{"utf8": "Ünïcode"}]}]},
        ensure_ascii=False,
    ).encode()
    assert parse_captions(json3, CaptionFormat.JSON3) == (TranscriptSegment("Ünïcode", 0, 0.005),)
    vtt = b"WEBVTT\n\n00:00.000 --> 00:00.005\n\xc3\x9cn\xc3\xafcode\n"
    assert parse_captions(vtt, CaptionFormat.VTT) == (TranscriptSegment("Ünïcode", 0, 0.005),)


def test_unknown_caption_format_is_rejected() -> None:
    with pytest.raises(CaptionError, match="Unsupported caption format"):
        parse_captions(b"data", "srv3")  # type: ignore[arg-type]
