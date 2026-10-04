# Obsidian Ingest

A personal, deterministic knowledge-ingestion CLI and Python learning project.
Currently supports YouTube transcripts, normalized source metadata, and
SponsorBlock-aware chapter/whole-source baseline chunk inspection. Model-assisted
semantic segmentation, deduplication, and Obsidian drafts are future work.
See [AGENTS.md](AGENTS.md) for architecture and project guidance.

## Usage

```bash
uv sync
uv run obsidian-ingest 'https://www.youtube.com/watch?v=VIDEO_ID'
```

The CLI prints timestamped transcripts. Metadata is available separately:

```python
from obsidian_ingest.extractor.metadata import categorize_content, fetch_metadata

metadata = fetch_metadata("https://www.youtube.com/watch?v=VIDEO_ID")
category = categorize_content(metadata)
print(metadata.title, category)
```

Metadata fetching does not retrieve captions. Calling both metadata and transcript
fetching currently performs separate extraction requests.

## Baseline Chunk Inspection

```bash
uv run obsidian-ingest --inspect-chunks 'https://www.youtube.com/watch?v=VIDEO_ID'
```

This opt-in path obtains metadata and captions from one yt-dlp extraction snapshot
and uses yt-dlp's SponsorBlock postprocessor to look up sponsor ranges. It does not
download or cut media. The default transcript command remains unchanged and does
not query SponsorBlock.

Only `sponsor` is excluded by default. Introductions, self-promotion, interaction
reminders, and other categories stay included. The explicit default category tuple
and provider lookup are in `extractor/providers/youtube/sponsorblock.py`; Python
callers can configure categories through `fetch_exclusions()` (an empty tuple
disables lookup). HTTP 404 means no matching ranges. Other lookup errors, malformed
responses, missing/invalid duration, or duration-mismatch warnings stop inspection;
they are not silently treated as videos without sponsors. Lookup uses the existing
yt-dlp transport and does not retry failures in this milestone.

Caption events remain atomic: a segment is excluded only if its **start** is in
`[range.start, range.end)`. A segment beginning before a sponsor and extending into
it stays whole; one beginning inside and extending past it is excluded whole.
This simple rule can retain some sponsor wording at boundaries or remove useful
wording at the end. The report flags boundary crossings; no text is trimmed.
yt-dlp may snap SponsorBlock range endpoints near the video start/end and checks
the annotation's video duration. The inspector reports the ranges exposed by that
postprocessor, not untouched SponsorBlock API intervals.

`CLIP` and `SHORT_FORM` keep the retained source whole. `LONG_FORM`, `EXTENDED`, and
unknown-duration sources use creator chapters when available, keeping unchaptered
gaps. Without chapters they remain one baseline chunk. Oversized sections are not
automatically split into arbitrary windows. Invalid, unordered, or overlapping
chapter intervals and transcript timing fail explicitly.

The report shows original millisecond timestamps, zero-based original index runs,
retained text, unchanged creator chapters (including empty ones), excluded ranges
and their segment attribution, and the lookup status/time. Chunk start/end spans
can contain intentional gaps; they do not mean continuous retained coverage.
Discarded text is absent from chunk output, but the original transcript remains
available through the Python result:

```python
from obsidian_ingest.segmentation import format_segmentation, inspect_source

result = inspect_source("https://www.youtube.com/watch?v=VIDEO_ID")
print(format_segmentation(result))
raw_transcript = result.filtered.original
retained_indices = result.filtered.retained_indices
```

Chunks use half-open positions into `retained_indices`, not slices into the raw
transcript. Filtering preserves timestamps, wording, and source order and accounts
for every original segment. These are **baseline chunks**, not model-validated
semantic chunks. No Laya, embeddings, classification, or note-writing calls occur.

## Metadata Assumptions

- The caller's URL is preserved as `original_url`. `canonical_url` uses yt-dlp's
  webpage URL, falling back to a watch URL constructed from the video ID.
- Publication timestamps are UTC-aware. When only an upload date is available,
  midnight UTC is a date-only placeholder, not a known publication time.
- Shorts detection uses parsed YouTube `/shorts/` URLs, not duration or aspect
  ratio. A Short supplied only through watch URLs may be classified as standard;
  `is_short=False` means no Shorts URL evidence was found, not verified absence.
- Missing optional fields remain `None`; tags and chapters become tuples.
  Chapters without a title, start, or end are omitted rather than guessed.
- Native vertical shorts are clips regardless of duration. Standard content is
  short-form below 8 minutes, long-form from 8 through 50 minutes inclusive, and
  extended above 50 minutes. Unknown standard duration yields no category.
- Instagram and TikTok hostnames are recognized, but extraction is not implemented.

## Verification

```bash
uv run ruff format src tests
uv run ruff check src tests
uv run pytest
git diff --check
```

Tests use fake metadata and mocked I/O, not live services. JSON3 caption events
remain atomic. VTT parsing is deferred: selection currently accepts VTT as a
fallback, but caption retrieval still expects JSON3.
