# Implementation Roadmap

This file tracks the remaining work to reach the project goal:

```text
URL -> unattended agent processing -> a few useful Markdown drafts in Obsidian
```

It is a prioritized implementation queue, not a record of completed work. Follow
the working procedure in [`AGENTS.md`](AGENTS.md): take the first actionable item,
plan and implement it, reread it as a revision checklist, verify the result, then
remove the completed item. Keep blocked or incomplete items here and explain the
remaining work. Do not mark an item complete based on implementation intent alone.

## Current Foundation

- YouTube extraction provides normalized metadata, English captions, chapters, and
  timestamps through yt-dlp. Current caption parsing handles JSON3, not VTT.
- `source.py` provides reusable ingestion and read-only overview, chapter, and
  bounded transcript access. One logical YouTube ingestion uses one yt-dlp metadata
  snapshot plus a caption request.
- `drafts.py` provides a create-only Markdown writer. It uses a configurable vault
  path and relative folder, defaulting to `00 Inbox/AI Drafts`; it adds source
  provenance and creates numbered filenames on collisions. It writes to a hidden,
  non-Markdown staging file and atomically publishes only completed drafts; forced
  termination may leave hidden staging data.
- The CLI currently prints transcripts or invokes the legacy `--inspect-chunks`
  baseline inspector. Neither path runs an agent or writes drafts.
- No vault reader, deduplication, agent orchestration, MCP server, or unattended
  workflow exists yet.

## Writer Integrity Follow-up

Finish the remaining writer checks before exposing it to an unattended agent. Keep
the writer create-only; never edit, append to, move, or delete existing notes.

- [ ] State the filesystem/platform assumptions and fail clearly if required safe
  no-follow primitives are unavailable.
- [ ] Review the threat model for concurrent movement of configured/open directories;
  document guarantees and limitations rather than claiming stronger confinement than
  the implementation provides.

## Restricted Vault Reads

Implement the smallest read surface needed for deduplication and related-note
suggestions. The user configures trusted scope; the agent cannot broaden it.

- [ ] Define trusted vault access settings: existing vault root, allowed read scope,
  configured draft destination, and explicit vault-relative excluded paths.
- [ ] Ensure the draft destination is within the vault and does not overlap an
  excluded path. Fail closed on invalid or conflicting settings.
- [ ] Add read-only operations to list accessible notes, read a selected note, and
  search accessible note text. Return bounded/paginated results rather than dumping
  the vault into one prompt.
- [ ] Include existing AI drafts in the accessible set so prior drafts can be
  considered for duplication.
- [ ] Apply allowed and excluded scope consistently to listings, reads, search,
  indexing, and metadata. Do not expose excluded paths through counts, suggestions,
  snippets, backlinks, or distinguishable errors.
- [ ] Reject traversal and symlink aliases that escape the vault or reach excluded
  content. Keep all note reads strictly read-only.
- [ ] Add offline temporary-vault tests for ordinary readable notes, configured
  private exclusions, symlink escapes, empty results, pagination, and attempts to
  read or write outside the allowed scope.
- [ ] Document the privacy limit: path exclusion cannot hide private information
  copied into an allowed note or guarantee secrecy from an agent already told it.

## Local Agent Adapter

Expose existing application functions through a small local MCP server, without
placing application policy in MCP-specific code.

- [ ] Choose and document the smallest local MCP SDK/transport that works with the
  selected agent; add only the necessary dependency.
- [ ] Expose coarse capabilities for source ingestion, overview/chapters, bounded
  transcript reads, restricted vault reads/search, and draft creation.
- [ ] Bind vault settings and the draft destination in trusted server configuration.
  Never accept an agent-selected vault root, arbitrary output path, or permissions.
- [ ] Bind provenance to the ingested source for the run; do not trust arbitrary
  source URLs or source metadata supplied by the agent when writing a draft.
- [ ] Keep each source available for the current run so navigation does not repeat
  yt-dlp extraction. Avoid a database or durable session infrastructure until needed.
- [ ] Bound tool outputs and return clear errors for invalid source references,
  invalid ranges, inaccessible notes, and failed writes.
- [ ] Test MCP operations offline and ensure every vault operation uses the same
  access policy as the underlying Python application functions.
- [ ] Do not expose generic filesystem mutation, shell access, or every helper as a
  tool.

## Agent Integration And Isolation

Use a replaceable general-purpose agent initially. Keep judgment in the agent and
filesystem/source correctness in the application.

- [ ] Connect a local agent, likely Hermes, to the restricted MCP tools.
- [ ] Configure the agent with only the tools needed for source exploration, vault
  comparison, and draft creation.
- [ ] Remove alternate unrestricted routes to the vault, including shell, general
  filesystem tools, and broadly privileged vault APIs.
- [ ] Choose an OS/container isolation arrangement that enforces the intended access
  if the agent tries to bypass tool-level rules. Keep excluded/private folders out
  of the agent process's accessible filesystem view where feasible.
- [ ] Verify which agent subprocesses and MCP processes are covered by the chosen
  isolation; do not treat prompts, tool names, or agent allowlists as the boundary.
- [ ] Treat source captions, web metadata, and retrieved note text as untrusted input
  that cannot alter tool permissions or trusted configuration.
- [ ] Keep provider/model selection replaceable and avoid requiring separate
  classifier and writer models.

## Agent Workflow And Deduplication

Let the agent decide what deserves a note. Deduplication should avoid redundant
drafts without rejecting useful additions or different explanations of an existing
topic.

- [ ] Define a small reusable application workflow that ingests one URL, exposes
  source navigation, compares accessible existing knowledge, and creates drafts.
- [ ] Let the agent inspect only relevant existing notes/search results rather than
  injecting the whole vault into its context.
- [ ] Instruct the agent to produce zero notes when appropriate and a small number
  of coherent, bite-sized notes otherwise.
- [ ] Prefer mental models, explanations, tradeoffs, gotchas, failure modes,
  workflows, practical lessons, and source insights over generic summaries or
  material easily found in official documentation.
- [ ] Distinguish redundant notes from a useful new perspective, explanation, or
  addition to an existing topic. Do not use filename collision as deduplication.
- [ ] Allow the agent to add a `Related Notes` section to a new draft with verified
  links to relevant accessible notes or suggested human edits. The writer must not
  follow those links to edit existing notes.
- [ ] Preserve source URL and useful timestamps in every draft; use the configured
  writer and never grant the agent direct write access to canonical notes.
- [ ] Handle small sources with a whole read and large sources with chapters or
  bounded reads. Processing windows are context management, not note boundaries.
- [ ] Do not place a multi-hour transcript into one prompt or imply one note per read.
- [ ] Define simple limits for run duration, tool calls, and transcript/context size
  based on the selected agent integration.
- [ ] Keep agent instructions focused on this workflow; do not create a general
  note-template system or depend on perfect model adherence to a template.

## Unattended CLI Workflow

- [ ] Add minimal trusted configuration loading for vault location, draft folder,
  exclusions, and agent connection/model settings.
- [ ] Make `obsidian-ingest <URL>` run the reusable application workflow without
  interactive decisions.
- [ ] Preserve useful current transcript and legacy inspection behavior, choosing
  an uncomplicated way to keep those capabilities accessible.
- [ ] Report zero-note outcomes, created draft paths, failures, and partial completion
  clearly, with appropriate process exit status.
- [ ] Handle timeouts and interruption without modifying existing vault notes.
- [ ] Avoid blind workflow retries that create duplicate drafts after uncertain write
  outcomes; use the writer's returned path/result to report what actually happened.
- [ ] Add offline end-to-end tests using fake extraction, agent/tool responses, and
  temporary vaults. Do not call live models, YouTube, or other live services in tests.

## First Useful Release Checks

- [ ] Verify the full URL-to-draft path using a real short source with usable captions.
- [ ] Verify a source that should yield no notes.
- [ ] Verify a source with several chapters and a source without chapters.
- [ ] Verify an hour-scale source can be explored without sending the whole transcript
  in one prompt.
- [ ] Verify existing relevant notes are considered and useful additions can still
  be drafted with related links.
- [ ] Review factual fidelity, note usefulness, note count, provenance, and source
  timestamps manually. The quality target is preferable to manual note-taking, not
  exhaustive coverage or perfection.
- [ ] Confirm ordinary notes remain unchanged, private exclusions are not exposed,
  and drafts appear only in the configured destination.
- [ ] Update the README to match actual supported behavior and configuration.
- [ ] Run formatting, lint, tests, and `git diff --check`.

## Later, Only When Justified

These are possibilities driven by observed problems, not prerequisites for the first
useful workflow:

- [ ] Improve transcript search/navigation if real large sources confuse the agent.
- [ ] Add vault indexing or similarity retrieval if ordinary search misses important
  related knowledge or deduplication quality is inadequate.
- [ ] Add documentation-aware context if drafts repeatedly duplicate reference docs.
- [ ] Add conservative caption normalization if errors corrupt important terminology.
- [ ] Add additional yt-dlp providers when a concrete source need justifies the work.
- [ ] Add source caching/state if repeated extraction creates a demonstrated cost or
  reliability problem.
- [ ] Reconsider more sophisticated source partitioning only if observed navigation
  failures materially harm note quality.
- [ ] Consider specialized models only if real use demonstrates a clear advantage.

## Remote Inbox: Explicitly Out Of Scope For Now

Only consider after the local unattended workflow is useful and reliable.

- [ ] Add a personal Telegram URL inbox first; consider WhatsApp or other interfaces
  only if useful.
- [ ] Trigger the same local workflow, restrict authorized senders, and report
  success/failure without duplicating ingestion logic.
- [ ] Add only the queueing, wake/idle behavior, and remote infrastructure required
  by real usage.

The core workflow should remain independent of whether its URL came from a CLI,
local agent, or a future messaging/share interface.
