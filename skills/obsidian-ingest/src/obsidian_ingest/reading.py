"""Track ordered transcript coverage between acquisition and curated publication.

Each transcript page returns a ``read_token`` recording how many retained segments have
been read in order from the start. Passing the previous token with the next page
advances it; writers require a token that covers the whole transcript. The token lives
only in requests and responses, so CLI processes and an MCP server behave identically.
It is bound to the transcript's content, which a caller cannot compute without the
cache, but it is a guard against skipped reading, not a security credential.
"""

from __future__ import annotations

import hashlib
import re

from obsidian_ingest.extractor.transcript import format_timestamp
from obsidian_ingest.source import Source, read_transcript_entries

# Versioned coverage count plus a compact content-bound digest, not a secret signature.
_TOKEN = re.compile(r"rt1-(\d{1,7})-([0-9a-f]{16})")
# Sized like the largest token, so pages reserve room for it before fitting segments.
TOKEN_PLACEHOLDER = "rt1-9999999-" + "0" * 16


def _mac(source: Source, read_through: int) -> str:
    """Bind progress to retained atom indices, starts, and text, not a secret key."""
    digest = hashlib.sha256()
    digest.update(source.metadata.source_id.encode())
    for index, segment in read_transcript_entries(source):
        # NUL delimiters distinguish adjacent fields while retaining original atom text.
        digest.update(f"\0{index}\0{segment.start!r}\0{segment.text}".encode())
    digest.update(f"\0{read_through}".encode())
    return digest.hexdigest()[:16]


def issue_token(source: Source, read_through: int) -> str:
    """Encode retained-segment coverage for later transcript pages or publication checks."""
    return f"rt1-{read_through}-{_mac(source, read_through)}"


def token_progress(source: Source, token: str) -> int:
    """Return how many retained segments the token proves were read in order."""
    match = _TOKEN.fullmatch(token) if isinstance(token, str) else None
    if not match or _mac(source, int(match.group(1))) != match.group(2):
        raise ValueError(
            "INVALID_ARGUMENT: read_token does not belong to this source; pass the "
            "read_token from your latest read_transcript page exactly"
        )
    return int(match.group(1))


def advance_token(source: Source, token: str | None, offset: int, count: int) -> str:
    """Advance coverage when a page continues (or overlaps) what was already read."""
    read_through = 0 if token is None else token_progress(source, token)
    # Overlap is harmless, but skipping a gap must not advance contiguous coverage.
    if offset <= read_through:
        read_through = max(read_through, offset + count)
    return issue_token(source, read_through)


def require_full_read(source: Source, token: str | None) -> None:
    """Refuse a write unless the token covers every retained transcript segment."""
    entries = read_transcript_entries(source)
    if token is None:
        raise ValueError(
            "INCOMPLETE_READ: pass read_token from your last read_transcript page; read "
            "the whole transcript first (read_transcript from offset 0)"
        )
    read_through = token_progress(source, token)
    if read_through < len(entries):
        _, segment = entries[read_through]
        raise ValueError(
            f"INCOMPLETE_READ: only {read_through} of {len(entries)} transcript segments "
            f"were read (up to {format_timestamp(int(segment.start))}); continue "
            f"read_transcript with offset {read_through} and this read_token, then write"
        )
