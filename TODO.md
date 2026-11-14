# Implementation Roadmap

## Direction and Execution Gate

```text
Source URL -> acquisition -> agent-guided curation -> new drafts, edit suggestions, or nothing
```

Ship a dependable, evidence-aware YouTube alpha first. Visual evidence and Instagram
follow afterward; OpenCode validation is deferred and does not block alpha.
The skill is the entry point. CLI and MCP must have one-to-one application parity:
same operations, validation, limits, result data, error meanings, and side effects,
with only transport syntax/envelope differences. Python owns predictable acquisition,
cache, access policy, and create-only publication; the agent owns judgment and synthesis.

Restricted setups expose only this MCP and disable general shell/filesystem tools.
Trusted setups allow normal host tools and may use CLI or MCP. Either interface's
writer remains create-only; an agent with shell access can bypass it. Neither MCP
alone nor an installation prompt creates an OS sandbox.

**Current gate: live Hermes trials.** Code for the former parity, acquisition, and
curation-guidance milestones has been implemented under user approval and verified by
automated tests and diff review. The next actionable item is milestone 1 below. It
needs an explicitly authorized disposable Hermes profile, a disposable test vault, and
the user-provided herdr-app pane hook; without them, report the trial as blocked. Do
not implement all milestones at once. No staging, commits, or pushes without explicit
approval.

All unchecked work below is planned, not an available capability. Remove completed
items only after their acceptance scenarios, relevant tests, and diff review pass.

## Current Foundation

- The installable skill contains Python application functions, CLI, stdio MCP, and
  trusted settings. There is no bundled agent runner or scripted harness installer.
- YouTube English captions (JSON3, with VTT fallback), metadata, chapters, SponsorBlock
  filtering, and bounded transcript reads exist. Unusable caption responses fail
  explicitly as `INGEST_FAILED`.
- Source overviews carry `diagnostics`: caption language/origin/format/translation
  status, acquisition time/extractor version, cache reuse at ingest, and timing
  observations (extent, provider duration, gaps, overruns). These are not quality scores.
- The SQLite cache (schema v2) keeps one current capture per source/option key,
  including the original caption payload; refresh atomically replaces it, and failure
  keeps the old entry. Incompatible caches are rejected with reset steps, never deleted.
- CLI and MCP share `responses.py` operations, limits, results, and coded errors. Search
  cursors are stateless and bound to query, scope, and vault policy. Only identifiers
  (CLI paths vs MCP `note_id`) and envelopes differ.
- Vault scopes, verified generated links, and create-only drafts and `(EDIT)`
  suggestions exist. Canonical notes must remain untouched throughout this roadmap.
- `SKILL.md` guides candidate consolidation, existing-note comparison, and attribution.
- Hermes setup is the prompt in `references/hermes.md`; it has not been validated live.
- `videos.txt` is a plain URL manifest consumed by `scripts/evaluate.py`, which records
  MCP responses without an agent or draft writes.

## Before Public Alpha

### 1. Live Hermes Setup and Curation Trials

**Problem:** Automated tests establish deterministic behavior, not that the setup
prompt works in a real host or that the guidance produces useful Markdown and UX.

**Reuse/change surface:** `references/hermes.md`, `SKILL.md`, `videos.txt`, and Hermes
through the user-provided herdr-app pane hook. Fix concrete failures in the smallest
relevant place. No agent runner, installer framework, or scoring/benchmark framework.

- [ ] Validate the installation prompt end to end in an explicitly authorized
  disposable profile: restricted MCP-only and trusted CLI/MCP modes, explicit
  profile/scope/approval questions, preserved unrelated settings, backups, reported
  changes, effective fresh-session tools without dummy notes, repeat setup without
  duplicated registrations or broadened scope, and following the reported undo steps.
  Let Hermes discover its current commands; no version pin or fixed config schema.
- [ ] In the same pane workflow, verify launch from an unrelated directory,
  restart/cache reuse, and response spillover behavior for transcript and note pages.
- [ ] Feed user-selected manifest URLs one at a time. Inspect the actual tools, UX, and
  generated files in the disposable test vault: attribution/timestamps, usefulness,
  overlap, uncertainty and diagnostics use, related links, edit suggestions, zero-note
  choices, and preserved canonical notes. Distinguish failures from successful
  zero-note outcomes; never publish dummy drafts or retry known success.

**Dependencies/risks:** Needs user-authorized host configuration access, the pane hook,
and live sources; outcomes vary by model. Hermes must stop if requested restrictions
cannot be established. Record the version tested as an observation, not a version gate.

**Acceptance/done:** Setup succeeds in both modes, effective permissions match the
chosen mode, repeat setup preserves config, undo restores the prior configuration
without touching canonical notes or unrelated config, and the coding agent has read
the resulting Markdown and reported concrete outcomes and failures for user review.
No golden prose, numerical quality scores, fixed note counts, or exact repeatability.

**Deferred:** Generic host installers, arbitrary config migration, publication
idempotency absent demonstrated uncertain outcomes, classifiers, embeddings, automatic
semantic deduplication, mandatory critics, and claims of exhaustive vault search.

### 2. Validate and Publish the Scoped YouTube Alpha

**Problem/evidence:** Unit tests and packaging alone do not establish a usable public
installation. Release claims need a complete installed-skill trial.

**Reuse/change surface:** Stabilized skill, CLI/MCP, cache, setup prompt, hands-on trials,
distribution metadata, and documentation. No new product subsystem.

- [ ] Verify clean installation from the actual distributed skill artifact, launch
  outside the repo, restart/cache reuse, and update without losing trusted settings.
  Cache recreation is acceptable when its schema changes (reset steps are in the README).
- [ ] Run the installed-skill URL-manifest trial through Hermes in herdr-app with a
  test vault. Review usefulness, factual fidelity, timestamps, response bounds,
  zero-note behavior, draft paths, both setup modes, and canonical-note preservation.
- [ ] Review packaging, dependency declarations, licensing, and exclusion of private
  artifacts. Run required Ruff formatting/lint, pytest, and `git diff --check`.
- [ ] Publish only after installation, undo instructions, trials, and release limitations
  are verified. Scope alpha to Linux, English-caption YouTube, evidence diagnostics,
  existing-vault curation, create-only drafts, and human-reviewed edit suggestions.

**Dependencies/risks:** Live network/harness trials are separate from mocked automated
tests. Hermes end-to-end validation is required; other harnesses must not be advertised
as tested. Harness-neutral architecture does not guarantee identical host behavior.

**Acceptance/done:** A new Linux user can install, explicitly configure scope, curate a
source into attributed drafts or an explained zero-note result, and restore their
Hermes configuration using the setup prompt's documented undo steps.

**Deferred:** Visual processing, Instagram, STT, native Windows, and OpenCode validation
are not alpha gates.

## After Alpha: Optional Capabilities

### 3. Targeted Visual Evidence and Local OCR

**Problem:** Technical names and case-sensitive commands can be ambiguous in captions.
Targeted frames can help, but captions, labels, and OCR may disagree or be unreadable.

**Reuse/change surface:** Extend the current SQLite cache; add small media/frame and
optional OCR application functions, CLI/MCP adapters, skill guidance, and Hermes image
delivery only as needed. Expose every application operation through both CLI and MCP.

- [ ] Keep captions sufficient by default. When additional evidence is needed, reuse
  existing media/frames or download bounded short-form media into a disposable host-local
  cache. Use provider classification plus duration/size checks, not a Shorts/Reels label
  alone. Prefer targeted retrieval for long videos; no automatic whole-video fallback.
- [ ] Bound windows, frame count, resolution, execution time, retries, media size, and
  cache growth/retention through trusted settings. Choose practical defaults during
  implementation and verify them; file-size limits are not aggregate bandwidth quotas.
- [ ] Persist selected supporting frames as SQLite BLOBs with timestamps and provenance;
  export disposable files only when a harness needs paths. Keep full media outside
  SQLite and the vault. Retain raw text, OCR observations, supported/proposed corrections,
  and unresolved disagreements separately under the current source capture. A correction
  records original wording, proposed wording, timestamp/frame, and supporting observation;
  refresh invalidates it. This is evidence tracing, not versioning or a permanent archive.
- [ ] Prefer host vision via scoped image delivery. Offer optional local OCR on issued
  frame identifiers, not arbitrary paths. Evaluate Tesseract on real representative
  cases before selecting it. No bundled vision service, automatic paid inference,
  automatic OCR installation, or full-video OCR. Missing vision/OCR remains explicit.
- [ ] Add lightweight completed-artifact/checkpoint records and identity/settings-based
  reuse, not a job engine. Do not mistake partial files for successful operations.
- [ ] Test missing dependencies, unavailable vision, expired URLs, interruption, limits,
  stale media/settings, partial artifacts, repeated requests, and absolute timestamps
  after seeking/clipping. Test OCR error/disagreement/no-text cases, SQLite image
  refresh invalidation, disposable exports, and actual Hermes MCP image delivery.
  Exercise Python through CLI and inspect agent behavior through the app-pane workflow.

**Dependencies/risks:** FFmpeg and optional OCR are external programs. Host image routing
and auxiliary inference vary; local processing does not imply local model inference.
Burned-in captions may be auto-generated and are not automatically authoritative.

**Acceptance/done:** A Short is downloaded once when needed, inspected locally, and
reused. A long video receives a targeted check. A supported correction retains its
evidence until refresh/reset; failed inspection or conflicting readings leave uncertainty.
Demonstrate bounded behavior and useful text-only fallback without requiring visual
analysis for every source.

**Deferred:** Diagram discovery, full-video visual analysis/OCR, broad image preprocessing,
automatic factual validation, and media orchestration frameworks.

### 4. Public Instagram and Optional Local STT

**Problem:** Public reels may lack usable subtitle tracks. Optional local STT supplies
readable evidence without requiring a hosted transcription service.

**Reuse/change surface:** Add an Instagram normalizer and optional transcription module;
extend dispatch/cache identity only as needed. Reuse media caching, frames, OCR,
evidence records, navigation, and publication. No speculative provider framework.

- [ ] Support public single-video URLs first. Prefer suitable native captions; otherwise
  use explicitly enabled local STT. Keep dependencies and model downloads optional.
- [ ] Preserve original STT separately from corrections. Record engine/model/settings
  and media identity; reuse only matching completed results after interruption.
- [ ] Test unsupported carousels/login-only sources, identity, missing audio, empty or
  interrupted STT, absent dependencies/models, changed media/settings, timestamps,
  acquisition failure, visual disagreement, and YouTube/cache regressions.

**Dependencies/risks:** Provider availability, FFmpeg, transcription packages/model
weights, CPU/disk costs. Do not introduce hosted STT or authentication implicitly.

**Acceptance/done:** A public reel without subtitles yields a provenance-linked local
transcript and optional targeted check, resumes without repeating completed STT, and
works through both CLI/MCP while caption-only installations remain usable.

**Deferred:** Login/cookie automation, private content, carousels, other providers,
hosted STT, and background workers.

## Verification Workflow

**Python functionality:** Use CLI to exercise shared operations with fake extraction,
mocked I/O, and temporary vaults. Keep parser/filesystem unit tests and a small
model-free MCP transport check. No agent is needed to test deterministic behavior.

**Installation, Markdown, and UX:** The coding agent uses a user-provided herdr-app
hook to create/control a pane running Hermes. Give Hermes the installation prompt,
verify setup, feed user-selected manifest URLs one by one, and inspect the test vault
and returned outcomes. Record enough context to reproduce concrete failures (URL,
setup mode, model/harness, steps, output/error); do not build scoring infrastructure.

Use real troublesome sources selected by the user to inspect incorrect terms,
caption/visual disagreements, missing coverage, overlapping candidates, and legitimate
zero-note outcomes as features become available. Do not claim tools that are not yet
implemented or exact repeatability of prose. Live network/model runs are separate
from automated tests; do not use private vaults or copy private artifacts into the repo.

## Later, Non-Priority or Only When Justified

- [ ] Validate OpenCode end to end with a disposable vault and documented skill/MCP
  setup. Not a current priority or alpha dependency.
- [ ] Add prompt-based installation guidance for further harnesses as optional adapters,
  using host-supported mechanisms rather than version-pinned profile-conversion scripts.
- [ ] Expand providers/languages or native Windows support only for concrete demand.
- [ ] Improve transcript navigation, indexing, or semantic retrieval only after observed
  misses demonstrate the need. Consider specialized models only with demonstrated value.
- [ ] Add publication idempotency only if uncertain real outcomes cause duplicates;
  never substitute dummy-content heuristics or retry known successful writes.
- [ ] Consider unattended runners, scheduling, or remote inboxes only after the hosted
  skill is useful and reliable; reuse application functions rather than job frameworks.

## Product Non-Goals

Canonical-note mutation, mandatory wiki taxonomies/indexes/link counts, permanent storage
of every video, blanket fact-checking, and exact repeatability of LLM prose are not the
product contract. Cache migrations, evidence history, a custom agent runner, and a
quality-scoring framework are out of scope. Preserve exclusions, verified generated
links, source attribution, create-only drafts, and human-reviewed edit suggestions.
These application guarantees do not constrain bypasses through trusted host tools.
