# Obsidian Ingest

A personal tool being built to turn source URLs into useful Markdown drafts in an
Obsidian vault, without manually taking notes.

The goal is simple: give it a URL, walk away, and receive a small number of
bite-sized notes in `AI Drafts/` for later human review. Zero notes is a valid
outcome when there is nothing worth preserving. A few strong drafts are better
than exhaustive coverage or many weak notes.

Prefer explanations, mental models, tradeoffs, gotchas, workflows, practical
lessons, and source insights over generic summaries or copies of documentation.
The drafts do not need to be perfect, just useful enough to prefer them to writing
the notes manually.

## Current State

**Extraction and safe draft creation work today; unattended agent processing does
not exist yet.** The current CLI prints transcripts to the terminal and does not
create drafts.

Implemented:

- yt-dlp-based YouTube metadata and existing English caption retrieval.
- Normalized source URLs, creator/context metadata, timestamps, and chapters
  where available, exposed through Python functions.
- Reusable source ingestion and read-only transcript access by time range or chapter.
- A create-only Markdown draft writer with a configurable vault path and draft folder.
- Timestamped transcript output, preserving parsed JSON3 caption events as atoms.
- Optional SponsorBlock-aware baseline inspection for development.

Instagram and TikTok hostnames are recognized, but their extraction is not
implemented. Caption retrieval is not audio transcription. JSON3 captions are
parsed; although track selection accepts a VTT fallback, VTT parsing is not
implemented. Sources without usable English JSON3 captions may fail.

There is no agent/model integration, MCP server, vault reader, or vault
retrieval/deduplication yet.

## Usage Today

Requires Python **3.13+** and [uv](https://docs.astral.sh/uv/).
From the repository root:

```bash
uv sync
uv run obsidian-ingest 'https://www.youtube.com/watch?v=VIDEO_ID'
```

Replace `VIDEO_ID` with a real YouTube video ID. The command prints caption details
and timestamped text. It does not query SponsorBlock or write to an Obsidian vault.
Extraction requires network access and depends on source/caption availability.

## Python Source API

Ingest once, then inspect and navigate the normalized source without more network
requests:

```python
from obsidian_ingest.source import (
    get_source_overview,
    ingest_source,
    list_chapters,
    read_chapter,
    read_transcript,
)

source = ingest_source("https://www.youtube.com/watch?v=VIDEO_ID")
overview = get_source_overview(source)
print(overview.metadata.title, overview.content_category, overview.chapter_count)

chapters = list_chapters(source)
segments = read_transcript(source, start=120, end=300)
if chapters:
    first_chapter = read_chapter(source, 0)
```

`Source` is an immutable composition of the existing metadata and transcript
models. Chapters stay in `source.metadata.chapters`; original parsed captions stay
in `source.transcript`. No persistence, extra session ID, or SponsorBlock lookup is
required. YouTube ingestion uses one yt-dlp extraction snapshot plus a caption
request for both models.

`read_transcript(source)` returns all original segments. Optional `start` and `end`
are seconds and use a half-open `[start, end)` interval based on each segment's
**start timestamp**. Omitting a bound leaves that side unbounded. Segments crossing
a boundary stay whole: no wording, timestamps, durations, or order are changed.
Reads return tuples of existing `TranscriptSegment` objects, not formatted strings.
Bounds must be finite and nonnegative, with `end >= start`; equal bounds or ranges
with no matching segment starts return an empty tuple.

`list_chapters` returns unchanged creator chapters, or an empty tuple. `read_chapter`
uses a zero-based index and the same timestamp rule. Negative or out-of-range
indices raise `IndexError`; non-integer indices raise `TypeError`.

The CLI and standalone `fetch_metadata`/`fetch_transcript` functions remain available
and unchanged. Calling those two standalone functions separately still performs
separate extraction requests; use `ingest_source` when both are needed.

## Creating Drafts

The writer creates Markdown notes without editing or overwriting existing files.
It requires an existing vault directory and creates its configured relative draft
folder as needed. The default is `00 Inbox/AI Drafts`.

```python
from pathlib import Path

from obsidian_ingest.drafts import DraftWriterSettings, create_draft

settings = DraftWriterSettings(vault_path=Path("/path/to/vault"))
result = create_draft(
    settings,
    title="Why retries can amplify an outage",
    contents="Agent-written note content, with no required template.",
    source=source.metadata,
    timestamps=(120.5, 245.0),
)
print(result.path)
```

The title becomes the Markdown filename; the writer does not add a duplicate title
heading to the note body. It appends a readable `Source` section with the source
title, URL, creator when available, and supplied timestamps. Timestamps are seconds.
Invalid filename titles or unsafe folder paths are rejected. Existing filenames
are preserved; a new draft uses `Title (2).md`, then `Title (3).md`, as needed.

The agent-facing caller supplies note content, title, source metadata, and optional
timestamps, but not an output path. The configured vault and draft folder are trusted
application settings. Related-note suggestions and vault reading/search are planned
for agent integration; this writer does not discover related notes or change them.

## Intended Architecture

**Deterministic Python is the toolbelt and safety boundary. The agent is the brain.**

The intended workflow is:

```text
URL -> yt-dlp ingestion -> safe source-access tools
    -> agent explores and selects useful knowledge -> safe draft writer -> AI Drafts/
```

Python handles source access, provenance, and filesystem safety. The agent decides
what matters, which ideas belong together, how many notes to produce, and what to
write. The writer creates new files only inside the configured draft folder, which
defaults to `<vault>/00 Inbox/AI Drafts/`; it never edits existing notes. Drafts
include source URLs and optional timestamps.

The likely near-term integration is Hermes calling a small local MCP tool surface
to inspect metadata and chapters, read source material, and create drafts. This is
planned, not implemented. Model choices remain replaceable.

The workflow should accommodate a Reel or Short as well as a multi-hour course:
read tiny sources whole, navigate chapters or bounded windows for large sources,
and do not put an entire 12-hour transcript into one prompt. Processing windows
are not notes; many reads should still lead to a reasonably small set of drafts.
Sophisticated semantic segmentation is not a prerequisite.

## Legacy Development Tools

The existing experimental baseline inspector remains available:

```bash
uv run obsidian-ingest --inspect-chunks 'https://www.youtube.com/watch?v=VIDEO_ID'
```

It uses one yt-dlp metadata extraction snapshot, fetches captions and SponsorBlock
ranges, and prints a retained-source report. Only `sponsor` is excluded by default;
introductions, self-promotion, interaction reminders, and other categories remain.
No media is downloaded or cut, and the original normalized transcript remains
available in the Python inspection result.

Filtering excludes whole caption events based on their start timestamps, so sponsor
boundaries can retain sponsor wording or remove useful wording. Non-404 lookup
errors, malformed results, and duration warnings stop inspection rather than
silently treating the source as sponsor-free.

The baseline uses whole-source or creator-chapter sections, not model-selected
semantic boundaries. It does not automatically split oversized sections, call
models, or create notes. It is not the required architecture for the new workflow.

`scripts/evaluate.sh` is optional live inspection tooling for a manifest of YouTube
video IDs. It runs formatting, lint, and tests before collecting reports; it is not
an unattended note-generation workflow.

## Roadmap

1. Keep and refine deterministic yt-dlp ingestion.
2. Keep the source API small and expose it through agent-facing adapters when needed.
3. Add restricted vault reads and agent-driven related-note suggestions; suggested
   links belong in new drafts and never authorize edits to existing notes.
4. Connect a local agent, likely Hermes via MCP.
5. Make `obsidian-ingest <URL>` run the unattended workflow.
6. Use real sources, observe failures, and fix those concrete problems.

Later, if real usage justifies it: better transcript navigation/search, vault
retrieval/deduplication, documentation-aware context, additional yt-dlp-supported
providers, conservative caption normalization, or improved source partitioning.
These are not prerequisites for useful drafts.

A **remote inbox** is a later convenience feature and explicitly out of scope now:
share a URL from a phone to a Telegram bot (likely first, perhaps WhatsApp later),
trigger local processing, receive a success/failure report, and let the worker go
idle again. No bots, queues, wake-up mechanisms, or remote infrastructure are being
built now. The core workflow should remain independent of where the URL came from.

## Development

```bash
uv sync
uv run ruff format src tests
uv run ruff check src tests
uv run pytest
git diff --check
```

Automated tests use fake extraction data and mocked I/O, not live services. Live
source checks are optional and separate. See [AGENTS.md](AGENTS.md) for coding-agent
guidance, architectural boundaries, and verification expectations.
