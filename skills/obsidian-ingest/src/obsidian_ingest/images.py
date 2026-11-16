"""Identify acquired thumbnail formats and dimensions before transport delivery.

Header inspection avoids an image dependency and never trusts remote content-type labels.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """Identified transport MIME type, export extension, and positive pixel dimensions."""

    mime_type: str
    extension: str
    width: int
    height: int


def identify_image(data: bytes) -> ImageInfo:
    """Return the type and size of a JPEG, PNG or WebP image; ValueError otherwise.

    The type comes from the magic bytes, never from a server's content type.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR" and len(data) >= 24:
        # PNG's first IHDR chunk stores two big-endian 32-bit dimensions.
        width, height = struct.unpack(">II", data[16:24])
        return _checked(ImageInfo("image/png", "png", width, height))
    if data.startswith(b"\xff\xd8"):
        return _checked(_jpeg(data))
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _checked(_webp(data))
    raise ValueError("not a JPEG, PNG or WebP image")


def _checked(info: ImageInfo) -> ImageInfo:
    """Reject size-less headers before they reach image delivery."""
    if info.width < 1 or info.height < 1:
        raise ValueError("image has no size")
    return info


# Start-of-frame markers carry the image size; DHT (C4), JPG (C8) and DAC (CC) do not.
_JPEG_FRAME_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def _jpeg(data: bytes) -> ImageInfo:
    """Walk JPEG markers to a size-bearing frame header, rejecting missing/broken headers."""
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            raise ValueError("malformed JPEG")
        marker = data[position + 1]
        if marker == 0xFF:  # fill byte
            position += 1
            continue
        if marker in (0x01, *range(0xD0, 0xD9)):  # markers without a length
            position += 2
            continue
        (length,) = struct.unpack(">H", data[position + 2 : position + 4])
        if length < 2:
            raise ValueError("malformed JPEG")
        if marker in _JPEG_FRAME_MARKERS:
            if position + 9 > len(data):
                break
            height, width = struct.unpack(">HH", data[position + 5 : position + 9])
            return ImageInfo("image/jpeg", "jpg", width, height)
        # The segment length includes its own two bytes but excludes the marker.
        position += 2 + length
    raise ValueError("JPEG has no frame header")


def _webp(data: bytes) -> ImageInfo:
    """Read dimensions from lossy, lossless, or extended WebP chunk headers."""
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        # Lossy dimensions share their high two bits with scaling flags.
        width, height = struct.unpack("<HH", data[26:30])
        return ImageInfo("image/webp", "webp", width & 0x3FFF, height & 0x3FFF)
    if chunk == b"VP8L" and len(data) >= 25:
        # Lossless dimensions are packed as two 14-bit, minus-one values.
        bits = int.from_bytes(data[21:25], "little")
        return ImageInfo("image/webp", "webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8X" and len(data) >= 30:
        # Extended headers store minus-one dimensions in little-endian 24-bit fields.
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return ImageInfo("image/webp", "webp", width, height)
    raise ValueError("malformed WebP")
