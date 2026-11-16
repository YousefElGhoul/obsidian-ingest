import asyncio
import base64
import io
import json
from pathlib import Path

import pytest
from mcp.client import Client
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError, TransportError

from obsidian_ingest import mcp_server, source_cache
from obsidian_ingest.drafts import DraftWriterSettings
from obsidian_ingest.extractor.providers.youtube import thumbnail as youtube_thumbnail
from obsidian_ingest.extractor.providers.youtube.thumbnail import (
    MAX_THUMBNAIL_BYTES,
    ThumbnailError,
    fetch_youtube_thumbnail,
    thumbnail_urls,
)
from obsidian_ingest.images import identify_image

URL = "https://www.youtube.com/watch?v=video-123"


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


def test_fetch_falls_back_when_the_first_size_is_missing(monkeypatch, make_image):
    hq, mq = thumbnail_urls("vid")
    downloader = _install(monkeypatch, {hq: 404, mq: make_image("jpeg", 320, 180)})
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


def test_cli_and_mcp_return_the_same_thumbnail(
    monkeypatch, tmp_path, fake_extraction, cli_runner, make_image
):
    image = make_image("png", 480, 360)
    monkeypatch.setattr(
        "obsidian_ingest.responses.fetch_youtube_thumbnail",
        lambda video_id: (image, f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    db = ("--cache-db", str(tmp_path / "cache.db"))
    source_id = cli_runner.ok("ingest-source", URL, *db)["source_id"]

    saved = cli_runner.ok("get-thumbnail", source_id, *db)
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
    cli_runner.ok("get-thumbnail", source_id, *db)
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]

    assert cli_runner.fail("get-thumbnail", "missing", *db)["code"] == "SOURCE_NOT_FOUND"

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


def test_unavailable_thumbnail_is_a_coded_error(monkeypatch, tmp_path, fake_extraction, cli_runner):
    def unavailable(video_id):
        raise ThumbnailError("this video has no thumbnail")

    monkeypatch.setattr("obsidian_ingest.responses.fetch_youtube_thumbnail", unavailable)
    db = ("--cache-db", str(tmp_path / "cache.db"))
    source_id = cli_runner.ok("ingest-source", URL, *db)["source_id"]
    error = cli_runner.fail("get-thumbnail", source_id, *db)
    assert error == {"code": "THUMBNAIL_UNAVAILABLE", "message": "this video has no thumbnail"}

    monkeypatch.setattr(
        "obsidian_ingest.responses.fetch_youtube_thumbnail",
        lambda video_id: (b"<html>gone</html>", "https://i.ytimg.com/vi/x/hqdefault.jpg"),
    )
    error = cli_runner.fail("get-thumbnail", source_id, *db)
    assert error["code"] == "THUMBNAIL_UNAVAILABLE"


def test_source_cache_is_untouched_by_thumbnails():
    # Thumbnails are fetched on demand and never stored in the source cache.
    assert source_cache.SCHEMA_VERSION == 2
