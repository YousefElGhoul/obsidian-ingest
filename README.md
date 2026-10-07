# Obsidian Ingest

Obsidian Ingest is a Python project for turning source URLs into a small number of
useful Markdown drafts in an Obsidian vault. The intended experience is to provide a
URL, leave the workflow unattended, and later review drafts rather than take notes
manually. Drafts should favor practical explanations, mental models, tradeoffs,
gotchas, workflows, and useful source insights. Zero drafts is a valid outcome.

The design principle is **deterministic Python is the toolbelt and safety boundary;
the agent is the brain**. Python provides normalized source access and safe draft
creation. An agent will eventually decide what is worth preserving and what to write.

## Current Status

Source extraction/navigation, create-only draft writing, and a local stdio MCP server
are implemented. Hermes must be configured separately. Vault reading/deduplication,
agent orchestration, and the unattended URL-to-draft workflow are not implemented yet.

Current capabilities:

- yt-dlp-based YouTube metadata and existing English caption retrieval.
- Normalized source metadata, chapters, timestamps, and read-only transcript access.
- Create-only Markdown drafts with a configurable vault path and relative draft
  folder, defaulting to `00 Inbox/AI Drafts/`.
- CLI subcommands for transcript output, normalized metadata, and source inspection.
- MCP tools for source ingestion/navigation and create-only draft writing.

YouTube captions are retrieved, not transcribed. JSON3 parsing is implemented;
selection can accept VTT as a fallback, but VTT parsing is not implemented. Instagram
and TikTok hostnames are recognized, but extraction for those providers is not.

## Installation And Usage

Requires Python **3.13+** and [uv](https://docs.astral.sh/uv/). From the repository
root:

```bash
uv sync
uv run obsidian-ingest transcript 'https://www.youtube.com/watch?v=VIDEO_ID'
```

The current CLI does not run an agent. Available commands:

```bash
obsidian-ingest transcript URL  # Print timestamped captions
obsidian-ingest metadata URL    # Print normalized metadata as JSON
obsidian-ingest inspect URL     # Source and SponsorBlock diagnostic report
obsidian-ingest mcp --vault PATH [--draft-folder PATH]
```

Extraction requires network access and depends on source and caption availability.

### Python Source API

Ingest once, then inspect metadata and read retained transcript segments without
additional network requests:

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
print(overview.metadata.title, overview.chapter_count)

chapters = list_chapters(source)
segments = read_transcript(source, start=120, end=300)
if chapters:
    first_chapter_segments = read_chapter(source, 0)
```

For one YouTube ingestion, metadata and captions use one yt-dlp extraction snapshot
plus a caption request. Transcript range bounds are seconds, half-open `[start, end)`,
and based on segment start time. Segments crossing a boundary remain whole. Chapter
indices are zero-based; only creator-provided chapters are returned. Sponsor filtering
is enabled by default. Use `read_transcript(source, include_excluded=True)` or the
equivalent chapter option to read original atoms. The unmodified transcript is always
available at `source.transcript`. If SponsorBlock fails, ingestion continues, the
overview reports the failure, and all atoms remain in the default readable view.

### Python Draft Writer

The writer requires an existing vault directory and creates its configured relative
draft folder as needed. Existing notes are never changed. Filename collisions create
a numbered new file.

```python
from pathlib import Path

from obsidian_ingest.drafts import DraftWriterSettings, create_draft

settings = DraftWriterSettings(vault_path=Path("/path/to/vault"))
result = create_draft(
    settings,
    title="Why retries can amplify an outage",
    contents="Agent-written note content, without a required body template.",
    source=source.metadata,
    timestamps=(120.5, 245.0),
)
print(result.path)
```

The default destination is `<vault>/00 Inbox/AI Drafts/`; an alternative safe,
vault-relative folder can be supplied in trusted settings. The title becomes the
filename, not a duplicate top-level heading in the note. A readable source section
is appended with source identity, URL, creator when available, and optional timestamps.
Related-note suggestions and vault reads are not yet implemented.

The writer requires POSIX-style directory-relative filesystem operations,
`O_NOFOLLOW`/`O_DIRECTORY`, file syncing, and hard-link support on the destination
filesystem. It checks for required operating-system capabilities before creating
draft folders and fails clearly if they are missing or hard-link publication is
rejected. Linux is currently verified; native Windows support is out of scope. The
draft is atomically visible after publication, but this is not a guarantee that its
directory entry survives power loss. A forced termination or staging-cleanup error
may leave hidden, non-Markdown staging data.

Directory descriptors prevent symlink traversal while opening the configured path,
but do not prevent another process from moving an already-open vault or draft folder.
Such a move can cause a draft to be published in the moved directory while the
returned path points to its former location. Configure a trusted destination and do
not allow an untrusted process to concurrently move or replace its directory tree;
stronger isolation requires an operating-system boundary.

## Local MCP Server

Run the server from a terminal with an existing vault path:

```bash
uv run obsidian-ingest mcp --vault '/path/to/test-vault'
```

The process communicates over stdio and keeps ingested `Source` objects in a simple
in-memory map. Source handles work only while that server process is running; restarting
it clears them. No database or network listener is started. The configured vault and
draft folder are trusted server settings, not tool arguments.

The tools let an agent ingest a URL, inspect its overview and creator chapters, list
SponsorBlock ranges, read timestamped transcript pages or chapter pages, and create a
draft attributed to that ingested source. Sponsor filtering defaults to `sponsor` only;
reads return the retained view, with an option to include original atoms. SponsorBlock
errors are reported and ingestion continues with unfiltered text. Transcript reads
default to 200 segments and cap pages at 500. Page offsets preserve source order, and
each segment includes its original source index. Vault listing/search and deduplication
are not available yet, so the agent cannot compare against existing vault notes through
this server.

### Connect Hermes

Add a local stdio server to Hermes' `~/.hermes/config.yaml`, replacing both paths:

```yaml
mcp_servers:
  obsidian_ingest:
    command: uv
    args:
      - run
      - --directory
      - /path/to/obsidian-ingest
      - obsidian-ingest
      - mcp
      - --vault
      - /path/to/test-vault
    cwd: /path/to/obsidian-ingest
    timeout: 600
    connect_timeout: 30
    tools:
      include:
        - ingest_source
        - get_source_overview
        - list_chapters
        - list_exclusions
        - read_transcript
        - read_chapter
        - create_draft
      prompts: false
      resources: false
```

Restart/reload Hermes after changing its configuration. For a first experiment, use a
disposable test vault. The MCP server only constrains its own tools; other Hermes tools
or shell access may independently access paths available to the Hermes process.

Suggested first test prompt:

> Ingest this URL and inspect its metadata and chapters. Read only the transcript
> material needed to identify a few useful explanations, mental models, tradeoffs, or
> practical lessons. Zero notes is fine. Create concise drafts with useful timestamps.
> Do not repeat the filename as a top-level heading. You cannot inspect existing vault
> notes yet, so do not claim that you deduplicated against the vault.

Hermes configuration and live agent runs are intentionally left to the user.

## Source Inspection

Inspect normalized source metadata, creator chapters, SponsorBlock outcome, exclusions,
and retained transcript atoms without constructing chunks:

```bash
uv run obsidian-ingest inspect 'https://www.youtube.com/watch?v=VIDEO_ID'
```

Sponsor filtering is on by default and only excludes `sponsor` atoms by their start
timestamps. Other categories remain. If SponsorBlock lookup fails, ingestion continues
with the full transcript and the report shows the failure; it does not claim that
filtering succeeded. The original transcript and exclusion attribution remain in the
Python `Source`. The `transcript` subcommand prints the raw unfiltered captions. Neither
command calls a model or creates drafts.

## Inspecting MCP Responses

To see the source content and tool responses that an agent would receive, without
connecting an agent or creating drafts:

```bash
uv run python scripts/evaluate.py
```

By default this reads YouTube IDs from `videos.txt`, connects to the local MCP server
over stdio, and writes one JSON report per video in `outputs_archive/`. Each report
contains the available tool schemas, arguments and responses, overview, exclusions,
all retained transcript pages, and a read of the first creator chapter when available.
Transcript/chapter page references point into the recorded `calls` array, where each
response is stored once. This can produce large reports for long videos. The evaluator
does **not** call the draft writer or any model. It extracts live YouTube data; this is
not part of pytest.

Optional positional arguments select a manifest, an output directory (created if
missing), and the delay between videos:

```bash
uv run python scripts/evaluate.py videos.txt outputs_archive 2
```

## Development

```bash
uv sync
uv run ruff format src tests scripts
uv run ruff check src tests scripts
uv run pytest
git diff --check
```

Automated tests use fake extraction data and mocked I/O, not live services. See
[`AGENTS.md`](AGENTS.md) for coding-agent guidance and [`TODO.md`](TODO.md) for the
current implementation roadmap. The roadmap is maintained separately so it can be
used to resume work across sessions without turning this README into a task log.
