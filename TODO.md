# Implementation Roadmap

The product goal is:

```text
YouTube URL -> skill-guided agent -> a few useful Markdown drafts, or none
```

The installable skill is the user-facing entry point. The stdio MCP is the
recommended tool interface; deterministic Python owns source access, provenance,
configured vault policy, and create-only writing. The host agent owns relevance,
synthesis, and note count. There is no bundled agent runner.

## Current Foundation

- `skills/obsidian-ingest/` contains the installable skill, Python runtime, trusted
  settings template, interactive setup command, and stdio MCP entry point.
- YouTube extraction provides normalized metadata, English captions, chapters, and
  timestamps through yt-dlp. JSON3 parsing is supported; VTT parsing is not.
- The CLI preserves transcript, metadata, inspect, and MCP commands and exposes JSON
  equivalents for the MCP operations. CLI vault reads use relative paths and revisions.
- Persistent SQLite source snapshots preserve source IDs across MCP restarts.
- Trusted vault scopes provide bounded search/read, verified related links, and
  review-only `(EDIT)` suggestions. Draft publication is create-only and atomic.
- The exported Hermes trial is reviewed in `.agents-outputs/hermes-trial-review.md`.
  It produced useful note candidates and a zero-note decision, but exposed tool-call
  wrapper retries, two accidental test drafts, uneven note structure, and some
  overlapping candidates.

## Remaining Before Public Alpha

- [ ] Install this skill into the host Hermes profile and launch its bundled
  `mcp/server.py` from the installed skill directory.
- [ ] Verify the active ingestion profile exposes only the intended obsidian-ingest
  tools and has no alternate shell/filesystem/vault tools enabled. MCP restrictions
  apply only to operations routed through the MCP; OS isolation is optional defense
  in depth and remains user-controlled.
- [ ] Verify the 20 KiB transcript envelope limit against the active Hermes deployment
  and its spillover behavior.
- [ ] Run a post-packaging source trial through the installed skill and assess note
  usefulness, factual fidelity, timestamps, zero-note behavior, and whether the updated
  guidance produces sensible headings without overlong drafts.
- [ ] Review retry behavior in the actual harness. The MCP returns the successful path;
  do not retry a successful write. Consider idempotency only if real uncertain outcomes
  produce duplicates; dummy-content heuristics are not a safe substitute.
- [ ] Verify clean install, launch from an unrelated working directory, restart, and
  config preservation when updating the skill.
- [ ] Update setup instructions only from verified harness behavior; profile changes
  stay explicit and user-controlled.
- [ ] Run format, lint, tests, and `git diff --check` before release.
- [ ] Publish the scoped alpha. Do not claim a second harness is tested until one has
  been verified end to end.

## Later, Only When Justified

- [ ] Add VTT parsing or other providers when a concrete source need justifies them.
- [ ] Improve transcript navigation or add indexing only if observed curation trials
  show that bounded literal search or current source reads miss useful knowledge.
- [ ] Add an unattended agent runner or remote inbox only after the hosted-skill
  workflow is useful and reliable; continue to reuse the same application functions.
- [ ] Consider specialized models only if real use demonstrates a clear advantage.
