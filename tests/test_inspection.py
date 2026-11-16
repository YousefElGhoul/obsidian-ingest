from datetime import UTC, datetime

from obsidian_ingest.diagnostics import MAX_REPORTED_GAPS
from obsidian_ingest.extractor.metadata import (
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
)
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript
from obsidian_ingest.inspection import format_source_inspection, inspect_source
from obsidian_ingest.source import Source


def _source(lookup: ExclusionLookup) -> Source:
    url = "https://youtu.be/abc"
    metadata = normalize_youtube_metadata(
        {
            "id": "abc",
            "title": "Lesson",
            "duration": 600,
            "chapters": [{"title": "Creator chapter", "start_time": 0, "end_time": 6}],
        },
        url,
    )
    transcript = Transcript(
        "abc",
        "en",
        "en",
        True,
        (
            TranscriptSegment("Retained atom crossing into sponsor", 1.125, 2),
            TranscriptSegment("Sponsor text", 2.25, 0.5),
            TranscriptSegment("After sponsor", 4.125, 1),
        ),
    )
    filtered = filter_source_transcript(metadata, transcript, lookup)
    return Source(metadata, transcript, lookup, filtered)


def test_inspection_reports_filtered_view_without_chunks() -> None:
    lookup = ExclusionLookup(
        (ExcludedRange(2.25, 4.125, "sponsor", "SponsorBlock"),),
        ExclusionStatus.COMPLETE,
        "SponsorBlock",
        ("sponsor",),
    )
    report = format_source_inspection(_source(lookup))

    assert "Sponsor filtering: complete" in report
    assert "Segments: 3 original, 2 retained, 1 excluded" in report
    assert "Range 0: [00:02.250-00:04.125) sponsor (SponsorBlock); excluded indices: 1" in report
    assert "Chapter 0: Creator chapter [00:00.000-00:06.000); 2 retained segments" in report
    assert "[0] [00:01.125] Retained atom crossing into sponsor" in report
    assert "[2] [00:04.125] After sponsor" in report
    assert "Sponsor text" not in report
    assert "Chunk" not in report


def test_inspection_reports_filter_failure_and_unfiltered_transcript() -> None:
    lookup = ExclusionLookup(
        (),
        ExclusionStatus.FAILED,
        "SponsorBlock",
        ("sponsor",),
        error_message="SponsorBlock timed out",
    )
    report = format_source_inspection(_source(lookup))

    assert "Sponsor filtering: failed" in report
    assert "Sponsor filtering warning: SponsorBlock timed out" in report
    assert "Segments: 3 original, 3 retained, 0 excluded" in report
    assert "Sponsor text" in report


def test_inspection_reports_explicitly_disabled_filtering() -> None:
    report = format_source_inspection(
        _source(ExclusionLookup((), ExclusionStatus.DISABLED, "SponsorBlock", ()))
    )
    assert "Sponsor filtering: disabled" in report
    assert "Selected categories: none" in report
    assert "Sponsor ranges:\nNone." in report
    assert "Sponsor text" in report


def test_inspection_lists_excluded_index_runs_lookup_time_and_empty_retained_view() -> None:
    lookup = ExclusionLookup(
        (
            ExcludedRange(0, 6, "sponsor", "SponsorBlock"),
            ExcludedRange(4.125, 4.5, "sponsor", "SponsorBlock"),
            ExcludedRange(500, 510, "sponsor", "SponsorBlock"),
        ),
        ExclusionStatus.COMPLETE,
        "SponsorBlock",
        ("sponsor",),
        datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    report = format_source_inspection(_source(lookup))

    assert "Lookup time: 2026-01-02T03:04:05+00:00" in report
    assert "Range 0: [00:00.000-00:06.000) sponsor (SponsorBlock); excluded indices: 0-2" in report
    assert "excluded indices: 2\n" in report
    assert report.splitlines()[-1] == "No retained transcript atoms."
    assert "Range 2: [08:20.000-08:30.000) sponsor (SponsorBlock); excluded indices: none" in report
    assert "Chapter 0: Creator chapter [00:00.000-00:06.000); 0 retained segments" in report


def test_inspection_reports_unknown_metadata_truncated_gaps_and_overrun() -> None:
    lookup = ExclusionLookup((), ExclusionStatus.DISABLED, "SponsorBlock", ())
    metadata = normalize_youtube_metadata({"id": "abc", "duration": 100}, "https://youtu.be/abc")
    segments = tuple(
        TranscriptSegment(str(index), index * 100.0, 1) for index in range(MAX_REPORTED_GAPS + 3)
    )
    transcript = Transcript("abc", "en", "en", True, segments)
    source = Source(
        metadata, transcript, lookup, filter_source_transcript(metadata, transcript, lookup)
    )
    report = format_source_inspection(source)

    assert "Title: (unknown)" in report
    assert "Creator: (unknown)" in report
    assert f"Caption gaps: {MAX_REPORTED_GAPS + 2} total; first {MAX_REPORTED_GAPS} shown" in report
    assert "Captions extend 1101.0 s past provider duration" in report


def test_inspect_source_uses_the_default_sponsor_policy(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "obsidian_ingest.inspection.ingest_source", lambda url: calls.append(url) or "source"
    )
    assert inspect_source("https://youtu.be/abc") == "source"
    assert calls == ["https://youtu.be/abc"]
