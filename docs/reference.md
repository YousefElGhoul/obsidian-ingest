# Technical reference

CLI, MCP, Python APIs, and operational limits for Obsidian Ingest. Start with the
[user guide](../README.md) for installation and first use. See
[development](development.md) for checks and live trials.

## Permissions and Interface Parity

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
opaque, process-scoped `note_id` handles; the CLI uses vault-relative paths. Vault
search and read results include a relative `path` in both transports, plus `note_id`
in MCP. Write results return an absolute draft path in both transports. `source_id`
handles and search cursors work across both transports and restarts. One case is
not a transport difference: `diagnostics.acquisition.reused_from_cache` depends on
whether a given call reused the cache.

Python functionality is tested through CLI without an agent, with a small model-free
MCP check of adapter wiring. Installation UX and agent-produced notes are tested
through live harness sessions (see
[Testing the Workflow](development.md#testing-the-workflow)).

## Errors

Errors share stable codes across transports. MCP tool errors read `CODE: message`
(after the framework's `Error executing tool …:` prefix). CLI tool commands print
`{"error": {"code": "...", "message": "..."}}` to stderr and exit with status 1 for
operation errors (including parsed but invalid values such as `read-source --limit 0`),
or 2 for parser errors (coded `INVALID_ARGUMENT`). Results go to stdout as JSON;
`--help` prints help text, not JSON. The non-tool commands `transcript`, `inspect`,
`metadata`, `mcp`, and `setup` keep plain argparse behavior.

MCP arguments are validated strictly before any coercion: a boolean for an integer,
a numeric string, a JSON-encoded list, or an unknown or missing argument is rejected
as `INVALID_ARGUMENT: <field>: <reason>`, matching the CLI's argument checks.

| Code | Meaning |
|---|---|
| `INVALID_ARGUMENT` | Malformed argument, unsupported provider, URL without an extractable ID, a URL that cannot be fetched safely (`URL_REJECTED`), or a note that is not a Web Clipper page |
| `INVALID_CURSOR` | Malformed cursor, or one from a different query, scope, or vault configuration; restart the search |
| `SOURCE_NOT_FOUND` | Unknown `source_id`; ingest the URL first |
| `INGEST_FAILED` | Acquisition failed, including unusable captions; article URL failures include a reason after `Could not ingest source:` (see [Articles](#articles)) |
| `SOURCE_CACHE_ERROR` | Cache open/read/write failure, corrupt entry, or incompatible schema; the message includes reset steps |
| `NOTE_UNAVAILABLE` | Unknown, excluded, missing, or otherwise inaccessible note (deliberately indistinguishable) |
| `NOTE_CHANGED` | The note's revision no longer matches; search and read it again |
| `NOTE_NOT_LINKABLE` | The note path cannot be emitted as a safe wikilink |
| `THUMBNAIL_UNAVAILABLE` | The thumbnail could not be fetched within the bounds (missing, too large, not an image, or a network error) |
| `INCOMPLETE_READ` | A write's `read_token` is missing, does not cover the whole retained source, or predates a changed capture; continue reading from the named offset, or read again |
| `WRITE_FAILED` | Operating-system or runtime failure, such as rejected draft publication |
| `CONFIG_ERROR` | CLI only: trusted config missing or invalid (MCP fails at startup instead) |

## Installation And Usage

Requires Python **3.13+** and [uv](https://docs.astral.sh/uv/). To prepare the bundled
skill from the repository root:

```bash
uv sync --project skills/obsidian-ingest
uv run --project skills/obsidian-ingest obsidian-ingest transcript \
  'https://www.youtube.com/watch?v=VIDEO_ID'
```

The current CLI does not run an agent. Available commands:

```bash
obsidian-ingest transcript URL  # Print timestamped captions
obsidian-ingest metadata URL    # Print normalized metadata as JSON
obsidian-ingest inspect URL     # Source and SponsorBlock diagnostic report
obsidian-ingest mcp [--config TOML] [--cache-db PATH]
obsidian-ingest mcp --vault PATH [--draft-folder PATH] [--cache-db PATH]
obsidian-ingest ingest-source URL [--include-sponsors] [--refresh] [--cache-db PATH] \
  [--config TOML] [--verbose]
obsidian-ingest ingest-source --note VAULT_RELATIVE_PATH [--include-sponsors] \
  [--config TOML] [--cache-db PATH] [--verbose]
obsidian-ingest get-source-overview SOURCE_ID
obsidian-ingest list-sections SOURCE_ID
obsidian-ingest list-exclusions SOURCE_ID
obsidian-ingest read-source SOURCE_ID [--start SEC] [--end SEC] \
  [--include-excluded] [--offset N] [--limit N] [--read-token TOKEN]
obsidian-ingest read-section SOURCE_ID SECTION_INDEX \
  [--include-excluded] [--offset N] [--limit N]
obsidian-ingest get-thumbnail SOURCE_ID
obsidian-ingest search-vault [QUERY] [--scope title|content|both] \
  [--cursor TOKEN] [--limit N]
obsidian-ingest read-vault-note VAULT_RELATIVE_PATH \
  [--revision SHA256] [--offset N] [--limit N]
obsidian-ingest create-draft SOURCE_ID CONTENTS [--filename NAME] \
  [--cite SOURCE_INDEX] [--related-note PATH REASON] --read-token TOKEN
obsidian-ingest create-edit-suggestion SOURCE_ID PATH REVISION SUMMARY REASONING CONTENT \
  [--cite SOURCE_INDEX] --read-token TOKEN
```

The six source navigation commands (`get-source-overview`, `list-sections`,
`list-exclusions`, `read-source`, `read-section`, `get-thumbnail`) and both write
commands also accept `--cache-db PATH` and `--config TOML`. Vault search and note read
accept `--config TOML`. `--cite` and `--related-note` are repeatable. See
`obsidian-ingest COMMAND --help` for details.

Vault-aware CLI commands use the trusted configuration at
`skills/obsidian-ingest/config/settings.toml` by default, independent of the current
working directory; pass `--config` to select another file. Source commands use those
default settings when present; clip captures require access through the originating
vault's settings. Run `obsidian-ingest setup` to set the absolute vault root and
explicitly choose the permitted scope interactively,
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
A readable source section is appended with source identity and URL. YouTube adds
`Creator` when known; articles add `Author`/`Authors`, `Site`, `Published` and `Language`
when known. The optional citation line is built from `cite`: YouTube lists up to eight
timestamp links, starting with the earliest and keeping the next only if it is at
least 30 seconds after the last kept one. Articles list up to eight unique section
names. Source titles and creator names are written as inert text: brackets,
backticks, pipes, angle brackets, `#`, `%%` and `==` are neutralized, control and
bidi-override characters are removed, and long values are cut to 300 characters.
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
absolute. It is outside the repository and vault. Use
`mcp --cache-db /path/to/sources.sqlite3` to override it; choose a trusted location
outside both the repository and vault.

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

The cache requires SQLite schema version 3 (`user_version`). Incompatible caches are
rejected with `SOURCE_CACHE_ERROR`, never migrated or deleted automatically; the MCP
server still starts and vault tools keep working. When upgrading from v0.1.x, start
with a fresh cache (or approve resetting the old one), then re-ingest sources.
In host allowlists, replace `list_chapters`, `read_transcript` and `read_chapter` with
`list_sections`, `read_source` and `read_section`, respectively. Update scripted writes
to cite source row indices (`cite` or `--cite`) instead of timestamps. To reset the
cache:

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

The source tools let an agent ingest a URL, inspect its overview and sections (creator
chapters for videos), list SponsorBlock ranges, read timestamped source pages
(`read_source`) or section pages (`read_section`), and create a draft attributed to that
ingested source. The overview reports `provider`, `kind` (`timed` for video), a
`section_count`, an `exclusions` block (status, categories and segment counts), the
`diagnostics` below, and a fixed `notice` that source text and metadata are untrusted
data. `list_sections` returns `{index, title, path, first_index, row_count}` plus
`start`/`end` seconds for videos. Sponsor filtering defaults to `sponsor` only;
reads return the retained view, with an option to include original atoms. SponsorBlock
errors are reported and ingestion continues with unfiltered text. Transcript reads
default to 100 segments and cap a request at 200. The serialized MCP content envelope is
limited to 20 KiB; a page may therefore contain fewer than the requested number of
whole segments. The response includes `fields` (`source_index`, `start`, `duration`,
`text` for videos; see [Articles](#articles) for text fields), `segments` as rows in
that order, `offset`, applied `limit` (at most 200), `total_segments`,
`remaining_segments`, and `next_offset`. Only contiguous `read_source` pages of the
retained view without time bounds advance `read_token` coverage. Start at offset 0,
then pass each returned `next_offset` (as `offset`) and `read_token` until `next_offset`
is `null`. Skipping ahead does not advance coverage; ranged and `include_excluded`
reads can pass an existing token through unchanged. `read_section` neither takes nor
returns a token. Tokens work across CLI processes and MCP, and writes require one
covering the whole retained source. A successful refresh or re-clip that replaces
the capture invalidates earlier tokens even if the rows match. A write with an older
token fails with `INCOMPLETE_READ`; read again from offset 0. Writes accept up to 200
supporting `source_index` values in `cite`; unknown or excluded indices fail with
`INVALID_ARGUMENT`, and duplicates are ignored.
Source indices, wording, timestamps, and order are preserved. An individual atom that
cannot fit by itself fails explicitly rather than being truncated. Transcript/chapter pages
are returned as one compact JSON text result instead of duplicated text and structured
payloads; the evaluator parses this result format. Earlier live trials recorded a
largest result of 13,170 characters (a 200-row transcript page), with nothing spilled
under Hermes' observed default `mcp_result_size_chars` threshold of 50,000.
That is a trial observation, not a bound on future results. Other deployments may
set a different threshold.

For `create_draft`, start `contents` with an H1 that is the note's title; the optional
`filename` argument defaults to the H1's kebab-case slug (see the draft writer above).
Do not include a mid-body Source/Sources/Source Attribution/Provenance section or an
agent-authored `## Related Notes` section; a final agent-written source section is
dropped. The application appends one `## Source`
section with the source URL, provider-specific attribution, and citations. A separate
`## References` section for additional papers and materials is allowed. Heading
validation covers ordinary ATX and Setext Markdown headings and ignores fenced or
indented code examples; it is not a general-purpose Markdown parser.

### Source Diagnostics

`ingest_source` (CLI `ingest-source`) returns `overview.diagnostics`.
`get_source_overview` (CLI `get-source-overview`) returns the overview directly, with
top-level `diagnostics`. For articles, `diagnostics.evidence.quality.status` is `ok`
or `degraded`, and `diagnostics.timing` is `null`. For videos:

- `evidence`: `kind` (`captions` for video), `language`, `origin` (`manual` or `generated`), `format` (`json3` or
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

For video diagnostics, `null` means unknown. Timing values are observations, not
quality scores: a gap may be music, silence, or on-screen text and does not prove
missing speech; no gap does not prove complete or accurate captions. Provider
durations can be wrong. Measured media duration is not collected.

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
scope paths are relative to the vault. Explicit allowances must include the draft
folder or a folder that contains it. Startup rejects invalid scopes and a draft
destination outside the accessible, non-excluded area. Unknown config keys are errors.

The source and configured vault tools include:

- `get_thumbnail(source_id)` optionally fetches the video's thumbnail
  (480x360 `hqdefault`, falling back to 320x180) from `i.ytimg.com`, built from the video
  ID only: a 10 s
  timeout, at most 2 MiB, with the type taken from the image bytes. MCP returns the image
  plus `{mime_type, width, height, url}`. The CLI writes the file to a disposable
  `$XDG_CACHE_HOME/obsidian-ingest/thumbnails/` (never the vault) and prints its `path`
  with the same fields. Thumbnails are not cached in SQLite and are not part of ingest.
  This source tool is also available without vault read configuration.
- `search_vault(query="", scope="both", cursor=None, limit=10)` searches note titles
  and/or content; an empty query browses accessible notes, including old AI drafts.
  Query length is limited to 256 characters. `scope` is `title`, `content`, or `both`;
  pages contain at most 20 notes and use an opaque `next_cursor`. Note IDs (`note_`
  plus 12 hex digits) are server-process scoped. Revisions are `sha256:` plus the
  first 16 hex digits of the file's SHA-256; a malformed revision is `INVALID_ARGUMENT`,
  a stale one `NOTE_CHANGED`. Cursors are stateless continuation tokens, not credentials:
  they survive restarts and work across CLI and MCP, are bound to the query, search
  scope, and current vault access policy (root, allowed, excluded), and fail with
  `INVALID_CURSOR` once any of those changes. Every page re-applies the current scope
  and exclusions; pagination is offset-based, so results can shift if notes are added
  or removed between pages. Search is literal and deterministic, not a semantic ranking:
  each query word is matched on its own (case-insensitive, whole words only) in
  the filename and content, and notes that match more distinct words come first. Each
  result lists its `matched_terms`.
- `read_vault_note(note_id, offset=0, limit=4000, revision=None)` returns a bounded
  Markdown character range. `limit` must be 1–8,000 characters. Continue with
  `next_offset` and the returned `revision` (required when `offset > 0`); changed
  notes require a fresh search/read.
  Both tools return compact JSON in one text result, bounded to 16 KiB.
- `create_edit_suggestion(source_id, target_note_id, target_revision, change_summary,
  reasoning, proposed_content, read_token, cite=None)` creates a review-only
  `edit-<target-title-slug>.md` draft.
  Pass the reviewed note's `revision` as `target_revision`. The draft starts with a
  Python-generated wikilink to the verified target and never modifies that target.

`create_draft` also accepts up to five `related_notes`, each containing a `note_id`
and a one-line `reason` of at most 300 characters, without wikilinks.
Python revalidates each target's current accessibility, existence, and
linkability at draft creation; ordinary related links are not tied to a content revision.
A legacy optional `revision` field is accepted but ignored. Python alone constructs the
generated `## Related Notes` section and its full vault-relative `[[path/to/note.md]]`
targets. Duplicate basenames are disambiguated by the full path. Body Markdown, including
ordinary Markdown links and user-authored wikilinks, is preserved but is **not verified**;
only links generated in `## Related Notes` and the first-line edit-suggestion target
link carry the server's accessible/existing-note guarantee.

The link syntax follows
[Obsidian's Internal links documentation](https://help.obsidian.md/Linking+notes+and+files/Internal+links):
it supports vault-root folder paths, `.md` extensions, and spaces. The docs warn that
`#`, `|`, `^`, `:`, `%%`, `[[`, and `]]` may break link targets. This writer marks paths
containing those tokens, backslashes, or control characters non-linkable instead of
inventing an escaping scheme. Notes with those paths can still be searched/read.
Unicode and ordinary punctuation are emitted literally; tests assert the actual
generated Markdown.

Unknown, excluded, expired, and otherwise inaccessible note IDs or paths all return the
same `NOTE_UNAVAILABLE` error. A changed note returns `NOTE_CHANGED`; malformed cursors
and arguments fail explicitly (see [Errors](#errors)). Vault search and read return
vault-relative note paths; draft and edit-suggestion writes return absolute paths.

These restrictions are enforced by the MCP server, not by the operating system. Other
Hermes tools, shell access, or processes with vault access can bypass them. For private
vault content, expose only this MCP surface to the curation agent and use OS/container
isolation to keep excluded paths out of its filesystem view where feasible. Exclusion
cannot hide private content already copied into an allowed note or guarantee secrecy
from an agent that has read it. Configure the vault tree against concurrent untrusted
renames/replacements while reading; descriptor-relative no-follow opens do not provide
a filesystem snapshot. Notes and transcripts remain untrusted input, not instructions
that grant permissions.

### Host Registration

Give an agent the skill's
[`SKILL.md`](../skills/obsidian-ingest/SKILL.md) and ask it to read it and follow its
instructions to install the skill and its tools. It follows
[`references/install.md`](../skills/obsidian-ingest/references/install.md): it asks for
the install location and vault scope, offers restricted mode only where supported,
shows its plan and waits for approval, backs up host config, runs setup with flags,
registers the MCP server, verifies in a fresh session, and reports changes and undo.
The harness notes linked there are hints, not version pins. See the
[tested scopes](../README.md#tested-harnesses) for validation dates.

The following YAML illustrates a Hermes registration; it is not a required host schema
or assumption about config location. For vault-aware curation, use trusted config
rather than legacy `--vault` mode:

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
        - list_sections
        - list_exclusions
        - read_source
        - read_section
        - get_thumbnail
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

## Articles

Any http(s) URL that no other provider claims is an article. Rows are
`[source_index, section, kind, text]`: `section` is the heading path (`A › B`), and
`kind` is `paragraph`, `list_item` (indented by nesting), `quote`, `code` (fenced, with
the language when the page gives it), `table_row` (`Header: value; …`), `image`
(`![alt](url)`, not downloaded) or `caption`. Text is Markdown with absolute http(s)
links; other link schemes become plain text. `list_sections` returns heading paths
with `first_index` and `row_count`; `start`/`end` reads apply only to videos.

**Fetching.** URLs are validated before any network access: http(s) only, no user
name or password, at most 2,048 characters, ports 80 and 443 (plus trusted
`extra_ports`), and no numeric-host tricks (`2130706433`, `0x7f.1`, `127.1`). Three
forms are kept apart: the original URL (shown in drafts), the fetch URL (IDNA host,
percent-encoded, no fragment, query kept as given), and the identity key (for the cache:
tracking parameters such as `utm_*`, `fbclid` and `gclid` removed, query sorted). Every
address a host resolves to must be public by default (see trusted settings below).
The connection is pinned to a checked address, with TLS hostname verification.
Each fetch follows at most five checked redirects (no HTTPS-to-HTTP downgrade) and
may retry once on HTTP 429 or 503 with a numeric `Retry-After` of at most 5 s and
time remaining. The transport rejects response headers over 64 KiB and decoded
bodies over 5 MiB. Body reads are charged by chunk, including chunks read before a
failure and during fallbacks, against 10 MiB raw and 20 MiB decoded per ingest.
One 60 s deadline bounds DNS and network I/O; extraction is checked before accepting
a result, not interrupted mid-computation. Environment proxies, cookies and
credentials are not used. Character sets follow the BOM, HTTP header, then
`<meta charset>`, with UTF-8 and charset detection as fallbacks.

**Extraction.** The HTML sanitizer removes scripts, styles, templates, comments, form
controls and elements marked `hidden`, `aria-hidden="true"`, or with inline
`display: none` or `visibility: hidden`, including `<html>` and `<body>`. It does not
evaluate stylesheet visibility. Page chrome (ads, sponsored and
promo blocks, newsletter and paywall prompts, related and share widgets, cookie
banners, comment sections) is pruned by whole class/id names, never blocks over 300
words. trafilatura then extracts the main text. Figure captions and code languages are
restored when available. The HTML renderer drops image alt text matching its
instruction-like heuristic; retained alt text is still untrusted.
`evidence.instruction_like` flags matching source row indices. Neither the flags nor
pruning guarantee that all hidden or instruction-like text was caught.

**Exclusions and quality.** Sponsorship disclosures (`sponsored`), short calls to action
(`promo`) and repeated blocks (`duplicate`, first copy kept) are excluded by default,
like video sponsor segments. `exclude_sponsors=false` (CLI `--include-sponsors`)
disables all three article exclusion rules. Quality failures are `NO_ARTICLE_TEXT`
(under 50 words or mostly links), `PAYWALL_SUSPECTED` (a paywall signal and a body under
half the declared `wordCount`) or `LOW_QUALITY` (mostly repeated). A paywall signal
alone produces a `paywall_signals` warning, not a paywall failure. Short readable
articles can pass with a `short_text` warning.

**Fallbacks.** When extraction fails or the page shows more text exists, the JSON-LD
`articleBody`, embedded JSON HTML (such as `__NEXT_DATA__`), the AMP page on the same
site, and this page's full-content entry in its own RSS/Atom feed are tried in order.
AMP and feed URLs must use the final page's host or a parent/subdomain relationship
on every hop, including redirects. On success, `evidence.attempts` lists the primary
extraction and completed fallback attempts in order, including refusals with a
reason and bounded message. Missing candidates are omitted, and no later fallback
is tried once one rates `ok`.

**Metadata.** Title, authors, published and modified dates, canonical URL, language,
site name, description and word count come from JSON-LD, then trafilatura, then
OpenGraph and `meta` tags, then `<title>`. `evidence.metadata_provenance` names each
field's source. `canonical_url` falls back to the final URL; when language is unknown,
metadata `language` is `null` and `diagnostics.evidence.language` is `und`. Drafts
add `Author`/`Authors`, `Site`, `Published` and `Language` when known, and cite up to
eight unique `Relevant sections` as escaped plain text.

**Failure reasons.** Article URL acquisition errors take the form
`INGEST_FAILED: Could not ingest source: <REASON>: …` (CLI JSON separates code and
message). Initial URL validation can instead return `INVALID_ARGUMENT`. The Web
Clipper hint accompanies `HTTP_401`, `HTTP_403`, `HTTP_429`, `HTTP_451`,
`CONNECTION_FAILED`, `NOT_HTML`, `TIMEOUT`, `NO_ARTICLE_TEXT`, `PAYWALL_SUSPECTED`
and `LOW_QUALITY` when returned as web acquisition errors. Fallback `NOT_HTML` records
the hint in attempt evidence rather than failing the ingest by itself; fallback
`TIMEOUT` fails the ingest with the hint. `URL_REJECTED` and `ADDRESS_BLOCKED` are
policy refusals without the hint. `DNS_FAILED`, `TOO_MANY_REDIRECTS`, `TOO_LARGE`,
and other HTTP statuses also carry no hint.

**Web Clipper notes.** `ingest_source(note_id=…)` (CLI `ingest-source --note PATH`)
reads a note through the vault policy and parses its frontmatter (`title`, `source`,
`author`, `published`, `lang`, `site`, `description`) and Markdown body into the same
row kinds. The note is never modified, its `source` must be a valid article URL, and
its total length must be at most 2,000,000 characters. `refresh` / `--refresh` applies
only to URL ingest. A clip shares the URL's capture for the same exclusion option:
re-clipping replaces it unless the originating vault, note path and revision all
match the stored clip. Every CLI/MCP operation loading a cached clip rechecks that
the configured vault is the originating vault (identified by its resolved root)
and can open that path. It does not recheck the note's revision; editing the note
does not update the capture. A direct source-ID call fails with `NOTE_UNAVAILABLE`
if the check fails. A URL ingest treats
an inaccessible clip as a miss and tries the web; a successful acquisition replaces
it, while a failure leaves it stored without returning its text. A successful URL
refresh also replaces it. Any replacement invalidates earlier read tokens.
The cache still stores clipped text: this check does not restrict direct access to
the database file.

**Settings and logs.** An optional `[articles]` table in the trusted settings accepts
`allow_private_network = true` and `extra_ports = [8080]`; no tool argument can change
them. The MCP server and `ingest-source --verbose` log fetch and extraction
outcomes to stderr using codes and counts, without page text or vault paths.

## Source Inspection

Inspect normalized source metadata, creator chapters, SponsorBlock outcome, exclusions,
and retained transcript atoms without constructing chunks:

```bash
uv run --project skills/obsidian-ingest obsidian-ingest inspect \
  'https://www.youtube.com/watch?v=VIDEO_ID'
```

Sponsor filtering is on by default and only excludes `sponsor` atoms by their start
timestamps. Other categories remain. If SponsorBlock lookup fails, ingestion continues
with the full transcript and the report shows the failure; it does not claim that
filtering succeeded. The normalized transcript and exclusion attribution remain in the
Python `Source`. The `transcript` subcommand prints unfiltered normalized captions,
not the original response bytes. Neither command calls a model or creates drafts.

For recording the MCP responses an agent would receive, see
[Inspecting MCP Responses](development.md#inspecting-mcp-responses).
