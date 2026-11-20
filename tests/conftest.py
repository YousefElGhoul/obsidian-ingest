import json
import struct
import sys
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from obsidian_ingest import cli, source_cache
from obsidian_ingest.extractor.metadata import ExcludedRange, ExclusionLookup, ExclusionStatus
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.source import Source

SAMPLE_URL = "https://www.youtube.com/watch?v=video-123"


@pytest.fixture(autouse=True)
def isolated_host_state(monkeypatch, tmp_path_factory):
    """Keep any default source cache, including in subprocesses, out of real host state."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("xdg-state")))


@pytest.fixture(autouse=True)
def no_live_article_fetches(monkeypatch):
    """Automated tests never use the network: the default article fetcher cannot resolve."""
    from obsidian_ingest.extractor.http import Fetcher, FetchError
    from obsidian_ingest.extractor.providers.article import provider

    def offline(host: str, port: int) -> list[str]:
        raise FetchError("DNS_FAILED", f"tests are offline ({host})")

    monkeypatch.setattr(provider, "make_fetcher", lambda: Fetcher(resolver=offline))
    monkeypatch.setattr(provider, "_settings", provider._settings)


@pytest.fixture
def sample_source() -> Source:
    """A 600 s source with one chapter, two atoms sharing a start, and one sponsor atom."""
    metadata = normalize_youtube_metadata(
        {
            "id": "video-123",
            "title": "A useful explanation",
            "description": "Context for the topic",
            "channel": "A creator",
            "duration": 600,
            "chapters": [{"title": "Main idea", "start_time": 0, "end_time": 20}],
        },
        SAMPLE_URL,
    )
    transcript = Transcript(
        "video-123",
        "en",
        "en",
        False,
        (
            TranscriptSegment("First atom", 0, 1),
            TranscriptSegment("Second atom at same time", 1, 0.5),
            TranscriptSegment("Third atom", 1, 1),
            TranscriptSegment("Sponsor atom", 2, 1),
        ),
    )
    lookup = ExclusionLookup(
        (ExcludedRange(2, 3, "sponsor", "SponsorBlock"),),
        ExclusionStatus.COMPLETE,
        "SponsorBlock",
        ("sponsor",),
    )
    return Source(
        metadata, transcript, lookup, filter_source_transcript(metadata, transcript, lookup)
    )


@pytest.fixture
def fake_extraction(monkeypatch, sample_source) -> MagicMock:
    """Replace provider extraction behind the source cache with the sample source."""
    extraction = MagicMock(side_effect=lambda url, exclude_sponsors=True: sample_source)
    monkeypatch.setattr(source_cache, "ingest_source", extraction)
    return extraction


@dataclass(frozen=True)
class CliRunner:
    monkeypatch: pytest.MonkeyPatch
    capsys: pytest.CaptureFixture[str]

    def run(self, *args: str) -> tuple[int, Any, dict | None]:
        """Run one CLI invocation; each call opens fresh cache and vault state like a new process."""
        self.monkeypatch.setattr(sys, "argv", ["obsidian-ingest", *args])
        code = 0
        try:
            cli.main()
        except SystemExit as error:
            code = error.code
        captured = self.capsys.readouterr()
        output = json.loads(captured.out) if captured.out.strip() else None
        error = json.loads(captured.err)["error"] if captured.err.strip() else None
        return code, output, error

    def ok(self, *args: str) -> Any:
        code, output, error = self.run(*args)
        assert (code, error) == (0, None)
        return output

    def fail(self, *args: str, exit_code: int | None = None) -> dict:
        code, output, error = self.run(*args)
        assert code != 0 and output is None and error is not None
        if exit_code is not None:
            assert code == exit_code
        return error


@pytest.fixture
def cli_runner(monkeypatch, capsys) -> CliRunner:
    return CliRunner(monkeypatch, capsys)


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IEND", b"")


def _jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def _webp(width: int, height: int) -> bytes:
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
    body += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


@pytest.fixture
def make_image() -> Callable[[str, int, int], bytes]:
    """Build a minimal valid image of a kind ("png", "jpeg", "webp") and size."""
    builders = {"png": _png, "jpeg": _jpeg, "webp": _webp}
    return lambda kind, width, height: builders[kind](width, height)
