# Obsidian Ingest

Obsidian Ingest is a harness-neutral Agent Skill with a Python runtime for turning
source URLs into useful new drafts, review-only edit suggestions, or nothing in an
existing Obsidian vault. You give a host agent a URL; it navigates the source with
these tools, decides what is worth keeping, and leaves drafts for you to review rather
than taking notes manually. It is an evidence-aware skill for a user-owned vault, not an
agent-owned wiki or an unattended pipeline. Drafts should favor practical explanations,
mental models, tradeoffs, gotchas, workflows, and useful source insights. Zero drafts
is a valid outcome.

The design principle is **deterministic Python is the toolbelt and safety boundary;
the agent is the brain**. The installable skill supplies the harness-neutral workflow;
Python provides normalized source access and safe draft creation. The host agent decides
what is worth preserving and what to write.

## Current Status

Source extraction/navigation, create-only draft writing, persistent caching, and scoped
vault search/read tools are implemented. Verified related-note links and review-only
edit suggestions are available with trusted settings. The installable skill and
stdio MCP are in `skills/obsidian-ingest/`; host configuration remains user-controlled.
There is no bundled agent runner. Use a dedicated profile with only this MCP for the
intended vault boundary; MCP policy does not restrict alternate host tools.

Current capabilities:

- yt-dlp-based YouTube metadata and existing English caption retrieval, parsing JSON3
  or, as a fallback, WebVTT.
- Normalized source metadata, chapters, timestamps, and read-only transcript access.
- Caption provenance and timing diagnostics in every source overview (see
  [Source Diagnostics](#source-diagnostics)).
- Create-only Markdown drafts with a configurable vault path and relative draft
  folder, defaulting to `00 Inbox/AI Drafts/`.
- CLI commands with one-to-one parity with the MCP operations, plus transcript output,
  normalized metadata, and source inspection.
- An installable `skills/obsidian-ingest/` skill with a skill-local stdio entry point
  and trusted TOML settings template.
- A persistent SQLite source cache with stable source handles across processes and
  restarts, retaining the original caption response alongside normalized atoms.
- Optional, trusted TOML vault scopes with bounded note search/read and verified related
  note and edit-suggestion drafts. Vault reads remain disabled with `--vault` alone.

YouTube captions are retrieved, not transcribed. Instagram and TikTok hostnames are
recognized, but extraction for those providers is not implemented.

### Current Limits and Product Boundary

- Reading all available captions does not establish complete video coverage or
  accurate transcription; diagnostics are observations, not quality scores.
- The SQLite database is a disposable cache, not a permanent evidence archive. Refresh
  keeps a source handle but replaces its capture; no history is kept.
- Media downloads, frame preparation, image retention, OCR, local STT, and structured
  correction records are not implemented. Do not assume these tools are available.
- Installation is agent-driven (see [Connect Hermes](#connect-hermes)). One live trial
  on Hermes v0.23.0 validated a restricted-mode install with a frontier model. Trusted
  mode, repeat setup, undo, and installing from a GitHub clone are not yet validated,
  and useful ingestion is not: in that trial a weaker model mostly stopped after
  ingesting to ask and skipped the vault search. No second harness is claimed as
  validated.

The intended contract is predictable operations, traceable evidence, explicit partial
results, and preservation of existing knowledge—not identical agent prose, exhaustive
deduplication, or independent verification of every source claim. The agent remains
responsible for relevance, synthesis, uncertainty, and whether to write anything.
Canonical notes are never automatically modified; this is not an agent-owned wiki.

The scoped alpha target is Linux and English-caption YouTube curation. Planned
correction and selected image evidence would belong in the current SQLite cache, and
optional full media in a disposable host-local media cache, not the vault; these are
not implemented. See [`TODO.md`](TODO.md) for the prioritized roadmap, acceptance
criteria, test workflow, and deferred integrations.

### Permissions and Interface Parity

- **Restricted setup:** Expose this MCP's curation tools and disable normal shell,
  filesystem, and other bypass tools. Application restrictions govern these operations;
  this is not an OS sandbox.
- **Trusted setup:** Keep normal host tools and use CLI or MCP as preferred. The agent
  can write files directly through those host tools, bypassing our application boundary.

Our CLI and MCP writers are create-only in either setup. There is no unsafe/destructive
writer mode; broader agent permissions are a host choice, not a transport feature.

CLI and MCP call the same application functions (`obsidian_ingest.responses`), so
operations, validation, limits, result data, error meanings, and side effects are
shared; only transport syntax, envelopes, and note identifiers differ. MCP issues
opaque, process-scoped `note_id` handles; the CLI uses vault-relative paths. Each
transport's search/read results carry only the identifier it accepts. `source_id`
handles and search cursors work across both transports and restarts. One case is
not a transport difference: `diagnostics.acquisition.reused_from_cache` depends on
whether a given call reused the cache.

Python functionality is tested through CLI without an agent, with a small model-free
MCP check of adapter wiring. Installation UX and agent-produced notes are tested
through actual Hermes (see [Testing the Workflow](#testing-the-workflow)).

### Errors

Errors share stable codes across transports. MCP tool errors read `CODE: message`
(after the framework's `Error executing tool …:` prefix). CLI tool commands print
`{"error": {"code": "...", "message": "..."}}` to stderr and exit with status 1 for
operation errors or 2 for invalid arguments (also coded `INVALID_ARGUMENT`); results go
to stdout as JSON. The non-tool commands `transcript`, `inspect`, `metadata`, `mcp`,
and `setup` keep plain argparse behavior.

MCP arguments are validated strictly before any coercion: a boolean for an integer,
a numeric string, a JSON-encoded list, or an unknown or missing argument is rejected
as `INVALID_ARGUMENT: <field>: <reason>`, matching the CLI's argument checks.

| Code | Meaning |
|---|---|
| `INVALID_ARGUMENT` | Malformed argument, unsupported provider, or URL without an extractable ID |
| `INVALID_CURSOR` | Malformed cursor, or one from a different query, scope, or vault configuration; restart the search |
| `SOURCE_NOT_FOUND` | Unknown `source_id`; ingest the URL first |
| `INGEST_FAILED` | Acquisition failed, including unusable captions |
| `SOURCE_CACHE_ERROR` | Cache open/read/write failure, corrupt entry, or incompatible schema; the message includes reset steps |
| `NOTE_UNAVAILABLE` | Unknown, excluded, missing, or otherwise inaccessible note (deliberately indistinguishable) |
| `NOTE_CHANGED` | The note's revision no longer matches; search and read it again |
| `NOTE_NOT_LINKABLE` | The note path cannot be emitted as a safe wikilink |
| `INCOMPLETE_READ` | A write's `read_token` is missing or does not cover the whole retained transcript; continue reading from the named offset |
| `WRITE_FAILED` | Operating-system or runtime failure, such as rejected draft publication |
| `CONFIG_ERROR` | CLI only: trusted config missing or invalid (MCP fails at startup instead) |

## Installation And Usage

Requires Python **3.13+** and [uv](https://docs.astral.sh/uv/). To prepare the bundled
skill from the repository root:

```bash
uv sync --project skills/obsidian-ingest
uv run --project skills/obsidian-ingest obsidian-ingest transcript 'https://www.youtube.com/watch?v=VIDEO_ID'
```

The current CLI does not run an agent. Available commands:

```bash
obsidian-ingest transcript URL  # Print timestamped captions
obsidian-ingest metadata URL    # Print normalized metadata as JSON
obsidian-ingest inspect URL     # Source and SponsorBlock diagnostic report
obsidian-ingest mcp [--vault PATH | --config TOML] [--draft-folder PATH] [--cache-db PATH]
obsidian-ingest ingest-source URL [--include-sponsors] [--refresh] [--cache-db PATH]
obsidian-ingest get-source-overview SOURCE_ID
obsidian-ingest list-chapters SOURCE_ID
obsidian-ingest list-exclusions SOURCE_ID
obsidian-ingest read-transcript SOURCE_ID [--start SEC] [--end SEC] [--include-excluded] [--offset N] [--limit N] [--read-token TOKEN]
obsidian-ingest read-chapter SOURCE_ID CHAPTER_INDEX [--include-excluded] [--offset N] [--limit N]
obsidian-ingest search-vault [QUERY] [--scope title|content|both] [--cursor TOKEN] [--limit N]
obsidian-ingest read-vault-note VAULT_RELATIVE_PATH [--revision SHA256] [--offset N] [--limit N]
obsidian-ingest create-draft SOURCE_ID CONTENTS [--filename NAME] [--timestamp SEC|MM:SS] [--related-note PATH REASON] --read-token TOKEN
obsidian-ingest create-edit-suggestion SOURCE_ID PATH REVISION SUMMARY REASONING CONTENT [--timestamp SEC|MM:SS] --read-token TOKEN
```

Source commands accept `--cache-db`; vault commands accept `--config`. `--timestamp`
and `--related-note` are repeatable. See `obsidian-ingest COMMAND --help` for details.

Vault-aware CLI commands use the trusted configuration at
`skills/obsidian-ingest/config/settings.toml` by default, independent of the current
working directory; pass `--config` to select another file. Run `obsidian-ingest setup`
to interactively set the absolute vault root and explicitly choose the permitted scope,
or pass `--vault-root PATH` with repeatable `--allow PATH` and `--exclude PATH` and an
optional `--draft-folder PATH`; with `--vault-root`, setup never prompts. Setup refuses
to overwrite existing settings. CLI note paths are vault-relative; pass
the returned `--revision` on continuation pages (`--offset` > 0). Ordinary related
links need no revision; edit-suggestion targets do.

`search-vault --cursor` continues a search from an earlier invocation or process, and
CLI and MCP cursors are interchangeable on the same vault configuration (see
[Restricted Vault Access](#restricted-vault-access)). Do not treat the first page as
an exhaustive search.

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
equivalent chapter option to read original atoms. The unfiltered normalized transcript
is always available at `source.transcript`; it is not a byte-exact copy of the caption
response. If SponsorBlock fails, ingestion continues, the overview reports the failure,
and all atoms remain in the default readable view.

### Python Draft Writer

The writer requires an existing vault directory and creates its configured relative
draft folder as needed. Existing notes are never changed. Filename collisions create
a new file with a `-2`, `-3`, … suffix.

```python
from pathlib import Path

from obsidian_ingest.drafts import DraftWriterSettings, create_draft

settings = DraftWriterSettings(vault_path=Path("/path/to/vault"))
result = create_draft(
    settings,
    contents="# Why retries can amplify an outage\n\nAgent-written note content.",
    source=source.metadata,
    timestamps=(120.5, 245.0),
)
print(result.path)  # .../00 Inbox/AI Drafts/why-retries-can-amplify-an-outage.md
```

The default destination is `<vault>/00 Inbox/AI Drafts/`; an alternative safe,
vault-relative folder can be supplied in trusted settings. Contents normally start with
an H1 that is the note's title; it may contain any characters and stays in the note
unchanged. The filename is the optional `filename` argument or, by default, that H1
(the first non-blank line, if it is an ATX `# ...` heading) converted to lowercase
kebab-case: Unicode letters and digits are kept, every other character becomes a word
break, and a trailing `.md` on a given `filename` is dropped first. So
`# Postgres: One DB, Many Jobs?` becomes `postgres-one-db-many-jobs.md`. The stem is
capped at 120 UTF-8 bytes, contains no path separators or dots, and reserved Windows
device names gain a `-note` suffix (`con-note.md`). Without a given filename or a
leading H1, or when the slug is empty, the call fails with `INVALID_ARGUMENT`.
A readable source section is appended with source identity, URL, creator when available, and optional timestamps.
YouTube timestamps link to the normalized video ID at the corresponding whole second;
timestamps for other providers remain plain time values.
Related-note links are verified against accessible notes when created. Vault search and
read operations are available through the configured MCP and CLI adapters.

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

Prepare trusted settings once, then launch the skill-local server:

```bash
uv run --project skills/obsidian-ingest obsidian-ingest setup
# Answer the vault/scope prompts, then:
uv run --project skills/obsidian-ingest python skills/obsidian-ingest/mcp/server.py
```

For legacy write-only mode, `obsidian-ingest mcp --vault PATH` remains available.

The process communicates over stdio and stores normalized extraction snapshots in a
local SQLite file using Python's standard-library `sqlite3`. No database server or
network listener is started. Source handles remain usable after MCP server or Hermes
gateway restarts, provided the server uses the same cache database. The configured vault,
draft folder, and cache database are trusted server settings, not tool arguments.

The default database is `$XDG_STATE_HOME/obsidian-ingest/sources.sqlite3`, or
`~/.local/state/obsidian-ingest/sources.sqlite3` when `XDG_STATE_HOME` is unset or not
absolute. It is outside the repository and vault. Use `mcp --cache-db /path/to/sources.sqlite3`
to override it; choose a trusted location outside both the repository and vault.

`ingest_source(url, exclude_sponsors=True, refresh=False)` returns `source_id`, `cached`,
and `overview`. A hit makes zero yt-dlp calls and no caption or SponsorBlock requests.
YouTube watch, short-link, shorts, live, and embed URLs share the video-ID cache key.
The stored original URL remains the URL used for extraction, even when a hit uses a
different URL form. Sponsor filtering enabled and disabled have separate snapshots and
opaque IDs: changing the option may require extraction once, but never silently reuses
the wrong filtering behavior. Each option-specific handle keeps its filtering semantics.

Each capture preserves all normalized metadata (including provider details and
publication time), normalized transcript atoms/timestamps and caption flags,
SponsorBlock lookup status/ranges/categories/retrieval time/errors, and acquisition
provenance (time, extractor and version, caption format and translation status). The
selected original caption response (JSON3 or VTT) is stored byte-for-byte in a separate
table with its SHA-256, not mixed into the normalized atoms. Signed caption URLs and raw
extractor dumps are not stored. The filtered view is reconstructed with the existing
filtering functions. Only source acquisition data is stored, not conversations,
generated drafts, or agent reasoning. Treat cached source content as untrusted data
just like live source content.

The cache keeps exactly one current capture per source/option key. There is no
automatic expiration and no history. Pass `refresh=True` to `ingest_source` (CLI
`--refresh`) to re-acquire an entry, including a previous failed SponsorBlock lookup.
The new capture is fully built first; then one transaction replaces the old capture
and everything keyed to it, keeping the existing `source_id`. A failed refresh reports
the failure and leaves the previous capture intact. Failed acquisition does not create
an entry. Unknown IDs produce `SOURCE_NOT_FOUND`; corrupt entries produce errors rather
than incomplete data, and refresh can replace a bad entry.

The cache schema is version 2 (SQLite `user_version`). An older (version 1) or
unrecognized database is rejected with `SOURCE_CACHE_ERROR`, never migrated or deleted
automatically; the MCP server still starts and vault tools keep working. To reset it:

1. Stop obsidian-ingest processes (MCP servers and CLI runs) using that cache.
2. Delete the file named in the error, by default
   `$XDG_STATE_HOME/obsidian-ingest/sources.sqlite3` or
   `~/.local/state/obsidian-ingest/sources.sqlite3`. A new cache is created on next use.
3. Ingest URLs again; old `source_id` handles become unknown.

Vault notes and published drafts are unaffected. Alternatively, point `--cache-db` at a
new path.

Python callers can use `SourceCache(path).ingest(url)` from
`obsidian_ingest.source_cache`, returning a `CachedSource` with `source_id`, `source`,
and `cached`; `SourceCache(path).get(source_id)` retrieves the normalized `Source` after
a restart. The low-level `source.ingest_source` API and diagnostic CLI commands remain
uncached and retain their existing behavior.

The source tools let an agent ingest a URL, inspect its overview and creator chapters,
list SponsorBlock ranges, read timestamped transcript pages or chapter pages, and create
a draft attributed to that ingested source. Sponsor filtering defaults to `sponsor` only;
reads return the retained view, with an option to include original atoms. SponsorBlock
errors are reported and ingestion continues with unfiltered text. Transcript reads
default to 100 segments and cap a request at 200. The serialized MCP content envelope is
limited to 20 KiB; a page may therefore contain fewer than the requested number of
whole segments. The response includes `fields` (`source_index`, `start`, `duration`, `text`), `segments`
as rows in that order, `offset`, requested `limit`, `total_segments`,
`remaining_segments`, `next_offset`, and (for in-order reads of the retained view)
`read_token`. The token is stateless, so it works the same across CLI processes and
MCP. It records how far the transcript has been read in order from offset 0, and draft
and edit writes require one that covers the whole retained transcript. Continue with the returned `next_offset` until it is `null`. Source
indices, wording, timestamps, and order are preserved. An individual atom that cannot
fit by itself fails explicitly rather than being truncated. Transcript/chapter pages
are returned as one compact JSON text result instead of duplicated text and structured
payloads; the evaluator parses this result format. This bound is intended to keep reads
small, but Hermes spillover thresholds vary by deployment and have not been verified
against a local Hermes installation.

For `create_draft`, start `contents` with an H1 that is the note's title; the optional
`filename` argument defaults to the H1's kebab-case slug (see the draft writer above).
Do not include a mid-body Source/Sources/Source Attribution/Provenance section or an
agent-authored `## Related Notes` section; a final agent-written source section is
dropped. The application appends one `## Source`
section with the source URL, creator when available, and supplied timestamps. A separate
`## References` section for additional papers and materials is allowed. Heading
validation covers ordinary ATX and Setext Markdown headings and ignores fenced or
indented code examples; it is not a general-purpose Markdown parser.

### Source Diagnostics

`ingest_source` and `get_source_overview` (CLI `ingest-source`, `get-source-overview`)
include `overview.diagnostics`, built by one shared function:

- `caption`: `language`, `origin` (`manual` or `generated`), `format` (`json3` or
  `vtt`), `translated`, and the retained original payload's byte size and SHA-256.
  `translated` is `true` only when yt-dlp's caption URL carries a translation language,
  `false` for an original automatic track, and `null` otherwise, including manual tracks.
- `acquisition`: `acquired_at` (UTC), `extractor`, `extractor_version` (yt-dlp's, or
  `null`), and `reused_from_cache`, known only at ingest and `null` on later reads.
- `timing`: segment count, first caption start, last caption end (the furthest end, so
  overlaps count), provider-reported duration, `gaps` without any caption atom of at
  least 30 s labeled `leading`, `internal`, or `trailing` (trailing only when the
  duration is known; at most 10 listed, `gap_count` exact, `gaps_truncated` flagged),
  and `overrun_seconds` when captions extend more than 2 s past the duration. Seconds
  are rounded to three decimals.

`null` always means unknown. Timing values are observations, not quality scores: a gap
may be music, silence, or on-screen text and does not prove missing speech; no gap does
not prove complete or accurate captions; provider durations can be wrong. Measured
media duration is not collected.

Caption acquisition fails explicitly with `INGEST_FAILED`, rather than producing an
apparently silent source, for an empty, non-UTF-8, malformed, or timed-text-free
response, a response over 16 MiB (a limit on bytes accepted, not a network quota), or
when no `json3`/`vtt` format is offered (the error lists those offered). VTT cues become
one atom each in file order; multiline text is joined with newlines, markup is removed,
entities are decoded, and overlapping or rolling-caption repetition is kept as delivered.

### Restricted Vault Access

Vault reading is disabled when the server is started with `--vault`. To enable it,
provide a trusted TOML file with `--config`:

```toml
[vault]
root = "/path/to/vault"
draft_folder = "00 Inbox/AI Drafts"
allowed = ["*"]
excluded = ["Private", ".obsidian", ".trash", ".git"]
```

Wildcard access covers Markdown notes recursively except excluded paths. If `excluded`
is omitted, it defaults to an empty list. Explicit allowances are also supported:

```toml
[vault]
root = "/path/to/vault"
draft_folder = "00 Inbox/AI Drafts"
allowed = ["Engineering", "Computer Science", "00 Inbox/AI Drafts"]
excluded = ["Engineering/Private"]
```

Excluded paths always win and exclude descendants. An explicitly allowed path that is
itself excluded (or lies inside an excluded path) is rejected with `Allowed path is also
excluded: <path>`; to hide part of an allowed folder, exclude a subfolder of it. All
scope paths are relative to the vault. With explicit allowances, include the draft folder if its notes should be
searchable. Startup rejects invalid scopes and a draft
destination outside the accessible, non-excluded area. Unknown config keys are errors.

The configured server adds three tools:

- `search_vault(query="", scope="both", cursor=None, limit=10)` searches note titles
  and/or content; an empty query browses accessible notes, including old AI drafts.
  Query length is limited to 256 characters. `scope` is `title`, `content`, or `both`;
  pages contain at most 20 notes and use an opaque `next_cursor`. Note IDs (`note_` plus 12 hex digits) are
  server-process scoped. Revisions are `sha256:` plus the first 16 hex digits of the
  file's SHA-256; a malformed revision is `INVALID_ARGUMENT`, a stale one `NOTE_CHANGED`. Cursors are stateless continuation tokens, not credentials:
  they survive restarts and work across CLI and MCP, are bound to the query, search
  scope, and current vault access policy (root, allowed, excluded), and fail with
  `INVALID_CURSOR` once any of those changes. Every page re-applies the current scope
  and exclusions; pagination is offset-based, so results can shift if notes are added
  or removed between pages. Search is literal and deterministic, not a semantic ranking:
  each query word is matched on its own (case-insensitive, whole words only) in
  the filename and content, and notes that match more distinct words come first. Each
  result lists its `matched_terms`.
- `read_vault_note(note_id, offset=0, limit=4000, revision=None)` returns a bounded
  Markdown character range. `limit` is capped at 8,000 characters. Continue with
  `next_offset` and the returned `revision`; changed notes require a fresh search/read.
  Both tools return compact JSON in one text result, bounded to 16 KiB.
- `create_edit_suggestion(source_id, target_note_id, target_revision, change_summary,
  reasoning, proposed_content, timestamps=None)` creates a review-only
  `edit-<target-title-slug>.md` draft.
  It starts with a Python-generated wikilink to the verified target and never modifies
  that target.

`create_draft` also accepts optional `related_notes`, each containing a `note_id` and a
short `reason`. Python revalidates each target's current accessibility, existence, and
linkability at draft creation; ordinary related links are not tied to a content revision.
A legacy optional `revision` field is accepted but ignored. Python alone constructs the
generated `## Related Notes` section and its full vault-relative `[[path/to/note.md]]`
targets. Duplicate basenames are disambiguated by the full path. Body Markdown, including
ordinary Markdown links and user-authored wikilinks, is preserved but is **not verified**;
only links generated in `## Related Notes` and the first-line edit-suggestion target link carry
the server's accessible/existing-note guarantee.

The link syntax follows [Obsidian's Internal links documentation](https://help.obsidian.md/Linking+notes+and+files/Internal+links): it supports vault-root folder paths, `.md` extensions, and spaces. The docs warn that `#`, `|`, `^`, `:`, `%%`, `[[`, and `]]` may break link targets. This writer conservatively marks paths containing those tokens, backslashes, or control characters non-linkable instead of inventing an escaping scheme. Notes with those paths can still be searched/read. Unicode and ordinary punctuation are emitted literally; tests assert the actual generated Markdown.

Unknown, excluded, expired, and otherwise inaccessible note IDs or paths all return the
same `NOTE_UNAVAILABLE` error. A changed note returns `NOTE_CHANGED`; malformed cursors
and arguments fail explicitly (see [Errors](#errors)). Vault tools return vault-relative paths only, never
absolute filesystem paths.

These restrictions are enforced by the MCP server, not by the operating system. Other
Hermes tools, shell access, or processes with vault access can bypass them. For private
vault content, expose only this MCP surface to the curation agent and use OS/container
isolation to keep excluded paths out of its filesystem view where feasible. Exclusion
cannot hide private content already copied into an allowed note or guarantee secrecy
from an agent that has read it. Configure the vault tree against concurrent untrusted
renames/replacements while reading; descriptor-relative no-follow opens do not provide
a filesystem snapshot. Notes and transcripts remain untrusted input, not instructions
that grant permissions.

### Connect Hermes

Give an agent a link to this skill's
[`SKILL.md`](skills/obsidian-ingest/SKILL.md) and ask it to install the skill and its
tools. It follows [`references/install.md`](skills/obsidian-ingest/references/install.md):
it asks once for the profile, restricted or trusted mode, and vault scope, shows its
plan and backs up host config, runs `obsidian-ingest setup` with flags, registers the
MCP server, restricts tools in restricted mode, verifies in a fresh session, and reports
changes and undo steps. Hermes commands observed on v0.23.0 are in
[`references/hermes.md`](skills/obsidian-ingest/references/hermes.md); they are hints,
not a version pin. See [Current Limits](#current-limits-and-product-boundary) for what
has been validated. The following YAML illustrates the resulting registration; it is
not a required host schema or assumption about config location. For vault-aware
curation, use trusted config rather than legacy `--vault` mode:

```yaml
mcp_servers:
  obsidian-ingest:
    command: /absolute/path/to/uv
    args:
      - run
      - --no-dev
      - --project
      - /path/to/obsidian-ingest/skills/obsidian-ingest
      - python
      - /path/to/obsidian-ingest/skills/obsidian-ingest/mcp/server.py
      - --config
      - /path/to/obsidian-ingest/skills/obsidian-ingest/config/settings.toml
    connect_timeout: 60
    tools:
      include:
        - ingest_source
        - get_source_overview
        - list_chapters
        - list_exclusions
        - read_transcript
        - read_chapter
        - create_draft
        - search_vault
        - read_vault_note
        - create_edit_suggestion
      prompts: false
      resources: false
```

The former version-pinned profile converter has been removed. Never create dummy notes
to test connectivity. Changes do not retroactively restrict an existing session. Host
configuration and live runs remain user-controlled.

To curate, send only the source URL in a fresh session; the skill treats a bare URL as
a complete request to run its whole workflow (ingest, read, search and compare vault
notes, then create drafts, edit suggestions, or nothing). Use only an explicitly
authorized profile and vault for configuration or live trials.

## Source Inspection

Inspect normalized source metadata, creator chapters, SponsorBlock outcome, exclusions,
and retained transcript atoms without constructing chunks:

```bash
uv run obsidian-ingest inspect 'https://www.youtube.com/watch?v=VIDEO_ID'
```

Sponsor filtering is on by default and only excludes `sponsor` atoms by their start
timestamps. Other categories remain. If SponsorBlock lookup fails, ingestion continues
with the full transcript and the report shows the failure; it does not claim that
filtering succeeded. The normalized transcript and exclusion attribution remain in the
Python `Source`. The `transcript` subcommand prints unfiltered normalized captions,
not the original response bytes. Neither command calls a model or creates drafts.

## Inspecting MCP Responses

To see the source content and tool responses that an agent would receive, without
connecting an agent or creating drafts:

```bash
uv run --project skills/obsidian-ingest python scripts/evaluate.py
```

By default this reads source URLs from `videos.txt`, connects to the local MCP server
over stdio, and writes one JSON report per URL in `outputs_archive/`, named
`mcp_eval_<first 16 hex digits of the URL's SHA-256>.json` (`.failed.json` for a failed
source). Reports from older runs named by video ID are neither overwritten nor
compared. Each report is keyed by `url` and
contains the available tool schemas, arguments and responses, overview, exclusions,
all retained transcript pages, and a read of the first creator chapter when available.
Transcript/chapter page references point into the recorded `calls` array, where each
response is stored once. This can produce large reports for long videos. The evaluator
does **not** call the draft writer or any model. It reuses cached sources or extracts
live YouTube data on a cache miss; this is not part of pytest.

This is a response recorder, not the primary Python verification path or an assessment
of note quality.

The manifest is UTF-8 plain text, read line by line with surrounding whitespace
stripped. Blank lines and lines starting with `#` are skipped. Every other line must be
exactly one absolute `http`/`https` URL with a host and no internal whitespace. Inline
comments are not supported, because `#` may belong to a URL fragment. A malformed or
duplicate line rejects the whole manifest with its line number (exit status 2). The
parser does not check provider support; an unsupported source fails in its own report.

Optional positional arguments select a manifest, an output directory (created if
missing), and the delay in seconds between sources:

```bash
uv run --project skills/obsidian-ingest python scripts/evaluate.py videos.txt outputs_archive 2
```

## Testing the Workflow

The workflow has two separate paths:

- **Python operations:** Shared functionality is exercised through CLI with fake
  extraction and temporary vaults, alongside parser/filesystem unit tests and a small
  model-free MCP stdio/schema/parity check. No agent runner is needed for deterministic
  behavior. Tests never open the real default cache.
- **Installation, Markdown, and UX:** Live trials run Hermes as a child agent in a
  throwaway profile with a disposable test vault: a frontier model installs the skill
  from its `SKILL.md` link, then a weaker model receives bare `videos.txt` URLs in
  parallel fresh sessions. The coding agent reads the transcripts and the generated
  Markdown. The protocol is in [`AGENTS.md`](AGENTS.md); reports stay in the gitignored
  `trial-reports/` directory. Current results are under
  [Current Limits](#current-limits-and-product-boundary).

No scoring framework, golden prose, or custom agent runner is needed. Inspect useful
notes, overlap, uncertainty, source attribution, zero-note decisions, and concrete
failures rather than expecting identical prose or note counts.

## Development

```bash
uv sync --project skills/obsidian-ingest
uv run --project skills/obsidian-ingest ruff format skills/obsidian-ingest/src tests scripts
uv run --project skills/obsidian-ingest ruff check skills/obsidian-ingest/src tests scripts
uv run --project skills/obsidian-ingest pytest
git diff --check
```

Automated tests use fake extraction data and mocked I/O, not live services. See
[`AGENTS.md`](AGENTS.md) for coding-agent guidance and [`TODO.md`](TODO.md) for the
current implementation roadmap. The roadmap is maintained separately so it can be
used to resume work across sessions without turning this README into a task log.
