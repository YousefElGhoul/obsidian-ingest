# Obsidian Ingest

A personal, deterministic knowledge-ingestion CLI and Python learning project.
Currently supports YouTube transcripts and normalized source metadata. AI,
segmentation, deduplication, and Obsidian draft generation are future work.
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
