# Development

Run commands from the repository root. User setup is in the [README](../README.md);
operations and API contracts are in the [technical reference](reference.md).

## Checks

```bash
uv sync --project skills/obsidian-ingest
uv run --project skills/obsidian-ingest ruff format skills/obsidian-ingest/src tests
uv run --project skills/obsidian-ingest ruff format scripts
uv run --project skills/obsidian-ingest ruff check \
  skills/obsidian-ingest/src tests scripts
uv run --project skills/obsidian-ingest pytest
git diff --check
```

Automated tests use fake extraction data and mocked I/O, not live services. See
[`AGENTS.md`](../AGENTS.md) for coding-agent guidance and [`TODO.md`](../TODO.md) for the
current implementation roadmap. The roadmap is maintained separately so it can be
used to resume work across sessions without turning the README into a task log.

For documentation-only changes, reread affected documents together, check claims
against code and the harness notes, verify relative links, and run `git diff --check`.

## Testing the Workflow

The workflow has two separate paths:

- **Python operations:** Shared functionality is exercised through CLI with fake
  extraction and temporary vaults, alongside parser/filesystem unit tests and a small
  model-free MCP stdio/schema/parity check. No agent runner is needed for deterministic
  behavior. Tests never open the real default cache.
- **Installation, Markdown, and UX:** Live trials use disposable vaults and fresh
  harness sessions. A frontier model installs the skill from its `SKILL.md` link,
  then a weaker model receives bare `videos.txt` URLs in parallel fresh sessions.
  Review the install and ingest transcripts and the actual generated Markdown.
  Hermes runs as an `hc` child in a throwaway profile. Other tested harnesses use
  the verified bubblewrap sandbox built by `scripts/trials/`. Never auto-approve a
  child outside an OS sandbox.

Read the [live-trial protocol](live-trials.md) and
[harness tables](live-trials-harnesses.md) before running trials. Read earlier reports
first; new reports stay in the gitignored `trial-reports/` directory. Use the
[report template](trial-report-template.md) and
[transcript audit prompt](trial-audit-prompt.md). Do not use a real vault or copy private
vaults, media, or credentials into the repository.

No scoring framework, golden prose, or custom agent runner is needed. Inspect useful
notes, overlap, uncertainty, source attribution, zero-note decisions, and concrete
failures rather than expecting identical prose or note counts. Current validated
scopes are in the [README](../README.md#tested-harnesses) and harness notes.

## Inspecting MCP Responses

To see the source content and tool responses that an agent would receive, without
connecting an agent or creating drafts:

```bash
uv run --project skills/obsidian-ingest python scripts/evaluate.py
```

Create your own `videos.txt` first (it is gitignored): one public URL per line, with
`#` comment lines allowed. By default the script reads source URLs from `videos.txt`, connects to the local MCP server
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
uv run --project skills/obsidian-ingest python scripts/evaluate.py \
  videos.txt outputs_archive 2
```
