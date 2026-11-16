from dataclasses import replace

import pytest

from obsidian_ingest.extractor.transcript import TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.reading import (
    TOKEN_PLACEHOLDER,
    advance_token,
    issue_token,
    require_full_read,
    token_progress,
)
from obsidian_ingest.source import Source


def _with_segments(source: Source, *segments: TranscriptSegment) -> Source:
    transcript = replace(source.transcript, segments=segments)
    filtered = filter_source_transcript(source.metadata, transcript, source.exclusion_lookup)
    return replace(source, transcript=transcript, filtered_transcript=filtered)


def test_issued_token_proves_its_progress_and_fits_the_placeholder(sample_source) -> None:
    token = issue_token(sample_source, 2)
    assert token.startswith("rt1-2-")
    assert token_progress(sample_source, token) == 2
    assert len(token) <= len(TOKEN_PLACEHOLDER)


@pytest.mark.parametrize(
    ("read_through", "offset", "count", "expected"),
    [
        (None, 0, 2, 2),
        (2, 2, 1, 3),
        (2, 1, 2, 3),
        (3, 0, 1, 3),
        (1, 2, 1, 1),
        (None, 1, 2, 0),
    ],
    ids=[
        "first-page",
        "next-page",
        "overlapping-page",
        "reread-keeps-progress",
        "skip-ahead-does-not-advance",
        "first-read-not-from-start",
    ],
)
def test_advance_only_counts_pages_contiguous_with_what_was_read(
    sample_source, read_through: int | None, offset: int, count: int, expected: int
) -> None:
    token = None if read_through is None else issue_token(sample_source, read_through)
    advanced = advance_token(sample_source, token, offset, count)
    assert token_progress(sample_source, advanced) == expected


@pytest.mark.parametrize(
    "token",
    [None, 5, "", "rt1-1", "rt1-x-0000000000000000", "rt1-1-000000000000000g", "rt2-1-" + "0" * 16],
    ids=["none", "int", "empty", "no-mac", "bad-count", "bad-hex", "other-version"],
)
def test_malformed_tokens_are_invalid_arguments(sample_source, token) -> None:
    with pytest.raises(ValueError, match="INVALID_ARGUMENT: read_token"):
        token_progress(sample_source, token)


def test_tampered_or_foreign_tokens_are_rejected(sample_source) -> None:
    token = issue_token(sample_source, 1)
    count_raised = token.replace("rt1-1-", "rt1-3-")
    other_source = replace(
        sample_source, metadata=replace(sample_source.metadata, source_id="other-video")
    )
    changed_text = _with_segments(
        sample_source,
        TranscriptSegment("Edited first atom", 0, 1),
        *sample_source.transcript.segments[1:],
    )
    for source, candidate in (
        (sample_source, count_raised),
        (other_source, token),
        (changed_text, token),
    ):
        with pytest.raises(ValueError, match="does not belong to this source"):
            token_progress(source, candidate)


def test_full_read_requires_a_token(sample_source) -> None:
    with pytest.raises(ValueError, match="INCOMPLETE_READ: pass read_token"):
        require_full_read(sample_source, None)


def test_partial_read_names_where_to_continue(sample_source) -> None:
    with pytest.raises(ValueError, match=r"only 1 of 3 .*up to 00:01.*offset 1") as raised:
        require_full_read(sample_source, issue_token(sample_source, 1))
    assert str(raised.value).startswith("INCOMPLETE_READ: ")


def test_full_read_counts_retained_segments_not_excluded_ones(sample_source) -> None:
    # Three of the four atoms are retained; the sponsor atom need not be read.
    require_full_read(sample_source, issue_token(sample_source, 3))
    with pytest.raises(ValueError, match="does not belong"):
        require_full_read(sample_source, "rt1-3-" + "0" * 16)


def test_empty_transcript_is_fully_read_by_a_zero_token(sample_source) -> None:
    empty = _with_segments(sample_source)
    require_full_read(empty, advance_token(empty, None, 0, 0))
