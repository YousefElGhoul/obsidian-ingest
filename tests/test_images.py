import struct

import pytest

from obsidian_ingest.images import identify_image

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


def _sof0(width: int, height: int) -> bytes:
    return b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"


def _riff(chunk: bytes) -> bytes:
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def _vp8(width: int, height: int) -> bytes:
    # Frame tag, start code, then 14-bit sizes whose top two bits are scaling flags.
    frame = b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", width, height)
    return _riff(b"VP8 " + struct.pack("<I", len(frame)) + frame)


def _vp8l(width: int, height: int) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    body = b"\x2f" + bits.to_bytes(4, "little")
    return _riff(b"VP8L" + struct.pack("<I", len(body)) + body)


@pytest.mark.parametrize(
    ("kind", "mime_type", "extension"),
    [("png", "image/png", "png"), ("jpeg", "image/jpeg", "jpg"), ("webp", "image/webp", "webp")],
)
def test_images_are_identified_by_their_bytes(make_image, kind, mime_type, extension):
    info = identify_image(make_image(kind, 480, 360))
    assert (info.mime_type, info.extension, info.width, info.height) == (
        mime_type,
        extension,
        480,
        360,
    )


@pytest.mark.parametrize(
    "data",
    [
        _vp8(480 | (1 << 14), 360 | (2 << 14)),
        _vp8l(480, 360),
        SOI + b"\xff" + b"\xff\xe0\x00\x02" + _sof0(480, 360) + EOI,
        SOI + b"\xff\x01" + b"\xff\xd0" + _sof0(480, 360) + EOI,
        SOI + b"\xff\xc4\x00\x04\x00\x00" + _sof0(480, 360) + EOI,
    ],
    ids=["webp-lossy-scaling-bits", "webp-lossless", "jpeg-fill-byte", "jpeg-rst", "jpeg-dht"],
)
def test_image_variants_report_their_real_size(data):
    info = identify_image(data)
    assert (info.width, info.height) == (480, 360)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "not a JPEG, PNG or WebP"),
        (b"<html>not found</html>", "not a JPEG, PNG or WebP"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR", "not a JPEG, PNG or WebP"),
        (SOI + EOI, "no frame header"),
        (SOI + b"\xff\xe0\x00\x10" + b"\x00" * 14 + EOI, "no frame header"),
        (SOI + b"\xff\xc0\x00\x11\x08", "no frame header"),
        (SOI + b"\x00\x00\x00\x00", "malformed JPEG"),
        (SOI + b"\xff\xe0\x00\x01\x00\x00", "malformed JPEG"),
        (_riff(b"XXXX" + b"\x00" * 20), "malformed WebP"),
        (_riff(b"VP8X\x0a\x00"), "malformed WebP"),
        (SOI + _sof0(480, 0) + EOI, "image has no size"),
    ],
    ids=[
        "empty",
        "html",
        "short-png",
        "jpeg-without-frame",
        "jpeg-app0-only",
        "truncated-sof",
        "jpeg-missing-marker",
        "jpeg-short-length",
        "unknown-webp-chunk",
        "short-vp8x",
        "zero-height",
    ],
)
def test_non_images_and_sizeless_images_are_rejected(data, message):
    with pytest.raises(ValueError, match=message):
        identify_image(data)


def test_zero_width_png_is_rejected(make_image):
    with pytest.raises(ValueError, match="image has no size"):
        identify_image(make_image("png", 0, 10))
