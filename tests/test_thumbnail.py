import asyncio
import base64
import io
import json
import struct
import zlib
from pathlib import Path

import pytest
from mcp.client import Client
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError, TransportError

from obsidian_ingest import cli, mcp_server, source_cache
from obsidian_ingest.drafts import DraftWriterSettings
from obsidian_ingest.extractor.metadata import ExclusionLookup, ExclusionStatus
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.extractor.providers.youtube import thumbnail as youtube_thumbnail
from obsidian_ingest.extractor.providers.youtube.thumbnail import (
    MAX_THUMBNAIL_BYTES,
    ThumbnailError,
    fetch_youtube_thumbnail,
    thumbnail_urls,
)
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.images import identify_image
from obsidian_ingest.source import Source

URL = "https://www.youtube.com/watch?v=video-123"


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


@pytest.mark.parametrize(
    ("data", "mime_type", "extension"),
    [
        (_png(480, 360), "image/png", "png"),
        (_jpeg(480, 360), "image/jpeg", "jpg"),
        (_webp(480, 360), "image/webp", "webp"),
    ],
)
def test_images_are_identified_by_their_bytes(data, mime_type, extension):
    info = identify_image(data)
    assert (info.mime_type, info.extension, info.width, info.height) == (
        mime_type,
        extension,
        480,
        360,
    )


@pytest.mark.parametrize("data", [b"", b"<html>not found</html>", b"\xff\xd8\xff\xd9", _png(0, 10)])
def test_non_images_and_sizeless_images_are_rejected(data):
    with pytest.raises(ValueError):
        identify_image(data)


def test_thumbnail_urls_are_built_from_the_video_id_on_the_fixed_host():
    assert thumbnail_urls("abc_DEF-123") == (
        "https://i.ytimg.com/vi/abc_DEF-123/hqdefault.jpg",
        "https://i.ytimg.com/vi/abc_DEF-123/mqdefault.jpg",
    )
    for bad in ("", "../etc", "a/b", "x" * 65, "evil.com/x"):
        with pytest.raises(ThumbnailError):
            thumbnail_urls(bad)


class _FakeDownloader:
    """Stands in for yt_dlp.YoutubeDL; replies per URL from a table."""

    def __init__(self, replies: dict[str, object]):
        self.replies = replies
        self.requested: list[str] = []

    def __call__(self, options):
        assert options["socket_timeout"] > 0
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def urlopen(self, request):
        self.requested.append(request.url)
        reply = self.replies[request.url]
        if isinstance(reply, int):
            raise HTTPError(Response(io.BytesIO(b""), request.url, {}, status=reply))
        if isinstance(reply, Exception):
            raise reply
        return io.BytesIO(reply)


def _install(monkeypatch, replies):
    downloader = _FakeDownloader(replies)
    monkeypatch.setattr(youtube_thumbnail.yt_dlp, "YoutubeDL", downloader)
    return downloader


def test_fetch_falls_back_when_the_first_size_is_missing(monkeypatch):
    hq, mq = thumbnail_urls("vid")
    downloader = _install(monkeypatch, {hq: 404, mq: _jpeg(320, 180)})
    data, url = fetch_youtube_thumbnail("vid")
    assert (url, identify_image(data).width) == (mq, 320)
    assert downloader.requested == [hq, mq]


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (404, "no thumbnail"),
        (500, "HTTP 500"),
        (TransportError("offline"), "request failed"),
        (b"x" * (MAX_THUMBNAIL_BYTES + 1), "exceeds"),
    ],
)
def test_fetch_failures_are_explicit(monkeypatch, reply, message):
    hq, mq = thumbnail_urls("vid")
    _install(monkeypatch, {hq: reply, mq: 404})
    with pytest.raises(ThumbnailError, match=message):
        fetch_youtube_thumbnail("vid")


def _sample_source() -> Source:
    metadata = normalize_youtube_metadata({"id": "video-123", "title": "T", "duration": 60}, URL)
    transcript = Transcript("video-123", "en", "en", False, (TranscriptSegment("Hi", 0, 1),))
    lookup = ExclusionLookup((), ExclusionStatus.COMPLETE, "SponsorBlock", ("sponsor",))
    return Source(
        metadata, transcript, lookup, filter_source_transcript(metadata, transcript, lookup)
    )


@pytest.fixture
def fake_extraction(monkeypatch):
    source = _sample_source()
    monkeypatch.setattr(source_cache, "ingest_source", lambda url, exclude_sponsors=True: source)


def _run(monkeypatch, capsys, *args):
    monkeypatch.setattr("sys.argv", ["obsidian-ingest", *args])
    code = 0
    try:
        cli.main()
    except SystemExit as error:
        code = error.code
    captured = capsys.readouterr()
    output = json.loads(captured.out) if captured.out.strip() else None
    error = json.loads(captured.err)["error"] if captured.err.strip() else None
    return code, output, error


def _ok(monkeypatch, capsys, *args):
    code, output, error = _run(monkeypatch, capsys, *args)
    assert (code, error) == (0, None)
    return output


def _fail(monkeypatch, capsys, *args):
    code, output, error = _run(monkeypatch, capsys, *args)
    assert code != 0 and output is None and error is not None
    return error


def test_cli_and_mcp_return_the_same_thumbnail(monkeypatch, capsys, tmp_path, fake_extraction):
    image = _png(480, 360)
    monkeypatch.setattr(
        "obsidian_ingest.responses.fetch_youtube_thumbnail",
        lambda video_id: (image, f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    db = ("--cache-db", str(tmp_path / "cache.db"))
    source_id = _ok(monkeypatch, capsys, "ingest-source", URL, *db)["source_id"]

    saved = _ok(monkeypatch, capsys, "get-thumbnail", source_id, *db)
    path = Path(saved.pop("path"))
    assert path == tmp_path / "xdg-cache/obsidian-ingest/thumbnails/video-123.png"
    assert path.read_bytes() == image
    assert saved == {
        "mime_type": "image/png",
        "width": 480,
        "height": 360,
        "url": "https://i.ytimg.com/vi/video-123/hqdefault.jpg",
    }
    # A second call replaces the file rather than accumulating copies.
    _ok(monkeypatch, capsys, "get-thumbnail", source_id, *db)
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]

    assert _fail(monkeypatch, capsys, "get-thumbnail", "missing", *db)["code"] == (
        "SOURCE_NOT_FOUND"
    )

    server = mcp_server.create_server(
        DraftWriterSettings(tmp_path / "vault"), cache_db=tmp_path / "cache.db"
    )

    async def exercise():
        async with Client(server) as client:
            result = await client.call_tool("get_thumbnail", {"source_id": source_id})
            assert not result.is_error
            picture, text = result.content
            assert picture.type == "image" and picture.mime_type == "image/png"
            assert base64.b64decode(picture.data) == image
            assert json.loads(text.text) == saved

    asyncio.run(exercise())


def test_unavailable_thumbnail_is_a_coded_error(monkeypatch, capsys, tmp_path, fake_extraction):
    def unavailable(video_id):
        raise ThumbnailError("this video has no thumbnail")

    monkeypatch.setattr("obsidian_ingest.responses.fetch_youtube_thumbnail", unavailable)
    db = ("--cache-db", str(tmp_path / "cache.db"))
    source_id = _ok(monkeypatch, capsys, "ingest-source", URL, *db)["source_id"]
    error = _fail(monkeypatch, capsys, "get-thumbnail", source_id, *db)
    assert error == {"code": "THUMBNAIL_UNAVAILABLE", "message": "this video has no thumbnail"}

    monkeypatch.setattr(
        "obsidian_ingest.responses.fetch_youtube_thumbnail",
        lambda video_id: (b"<html>gone</html>", "https://i.ytimg.com/vi/x/hqdefault.jpg"),
    )
    error = _fail(monkeypatch, capsys, "get-thumbnail", source_id, *db)
    assert error["code"] == "THUMBNAIL_UNAVAILABLE"


def test_source_cache_is_untouched_by_thumbnails():
    # Thumbnails are fetched on demand and never stored in the source cache.
    assert source_cache.SCHEMA_VERSION == 2
