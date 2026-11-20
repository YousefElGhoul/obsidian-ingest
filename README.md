<!--
---
portfolio:
  title: "Obsidian Ingest"
  subtitle: "AI Agent Skill That Turns Videos and Articles into Notes"
  type:
    - AI
    - Tooling
  status: "Active"
  time: "2026"

  description: "Send your AI agent a YouTube or article link and get back a few notes worth keeping, or none at all. The agent reads the whole transcript or article, checks your existing Obsidian vault for what you already know, and proposes new drafts or edit suggestions with timestamped source links. Python handles source access, vault scope, and a create-only writer that never touches your existing notes, so you stay in charge of what you keep. It is one skill shared by Claude Code, Codex, opencode, and Hermes, with matching CLI and MCP interfaces."

  skills:
    - Python
    - AI Agents
    - MCP
    - Agent Skills
    - LLM Tooling
    - CLI
    - SQLite
    - Pydantic
    - yt-dlp
    - uv
    - pytest
    - Ruff
    - GitHub Actions
    - Linux
    - Security

  live: null
  slug: "obsidian-ingest"
---
-->

# Obsidian Ingest

Turn a YouTube video or a web article into a few useful notes for your existing
Obsidian vault. An agent reads the captions or the article, searches your allowed
notes, and chooses new drafts, review-only edit suggestions, or nothing. You review
the result.

This is an alpha release: it works on the harnesses and scopes listed under
[tested harnesses](#tested-harnesses), and install and distribution are still being
verified.

This is an Agent Skill with a Python runtime, not a bundled agent or an unattended
pipeline. Python handles source access, provenance, vault scope, and create-only
writes. Your host agent decides what is worth keeping and how to explain it.

## Features

### Read the source before choosing ideas

The agent reads the whole retained English transcript in timestamped pages, preserving
caption wording and order; writes require a token covering that full read. Creator
chapters are available for navigation, and SponsorBlock filters `sponsor` segments
best-effort, reporting lookup failures rather than claiming filtering succeeded.

### Read articles, too

An http(s) link not claimed by another provider is read as an article. The fetch can
follow checked redirects, retry once, and try same-site AMP or feed fallbacks. The
extractor keeps the main text in ordered Markdown blocks with links and heading paths.
It prunes page chrome and explicitly hidden HTML, but does not evaluate stylesheet
visibility. Pruning and instruction-like flags are heuristics; retained text,
including image alt text, is untrusted. Sponsored, promo and repeated blocks are
excluded by default but still readable. Metadata records where its values came from;
the canonical URL falls back to the final URL, and diagnostics use `und` for unknown
language.

When the tool suggests clipping an unreadable page, save it with
[Obsidian Web Clipper](https://obsidian.md/clipper) and ingest the clipped note
instead; your browser has already rendered it. Private and local network addresses
are refused by default, and every redirect is checked. See the
[article reference](docs/reference.md#articles) for fetch limits and error reasons.

### Compare with what you already know

For each useful idea, the agent searches your allowed Markdown notes and reads the
closest matches before writing. When it supplies related-note targets, the writer
checks that they exist and are accessible before adding links with reasons to open them.

Those generated related-note links and edit-suggestion targets are verified. Ordinary
links inside agent-written prose are preserved, not checked by the writer.

### Get a draft, an edit suggestion, or nothing

A new idea becomes a draft; an addition to an existing note becomes a separate,
review-only edit suggestion containing the proposed changes. An already-covered or
weak idea produces nothing, rather than another summary to sort through.

It does not replace your vault with an agent-owned wiki. It does not need to write a
note for every video or chapter. Zero notes is a valid result.

The skill keeps ideas worth looking up again months later: techniques, mechanisms,
tradeoffs, gotchas, and mental models. It deliberately writes nothing for let's-plays,
vlogs, how a video game works, or general career and study advice.

### Keep existing notes unchanged

The create-only writer never edits, overwrites, moves, or deletes existing notes.
Both drafts and suggestions go to one configured folder, `00 Inbox/AI Drafts/` by
default; filename collisions get a numbered suffix instead of replacing a file.

### Choose what the application can read

Allow specific vault-relative folders or `*` for all Markdown notes, then exclude
private paths and their descendants. Exclusions win, and the draft folder must stay
inside the accessible scope; these are application boundaries, not host permissions.

### Trace ideas back to the source

The writer adds source identity and URL. Videos add the creator when known and
timestamp links for citations. Articles add authors, site, publication date and
language when known, and section names for citations. Source diagnostics expose
manual or generated caption origin, translation status, acquisition details, and
timing observations such as caption gaps; they do not certify the source's claims
or caption accuracy.

### Use your existing agent

One skill works with Hermes, Claude Code, Codex, and opencode in the
[tested scopes](#tested-harnesses). CLI and MCP use the same application operations,
validation, limits, and write behavior; their syntax and note identifiers differ.

### Reuse source acquisition, not a generated answer

A local SQLite cache reuses acquired source metadata, captions and article text across
sessions; tracking parameters such as `utm_*` don't create a second copy.
Vision-capable agents can optionally fetch a thumbnail on demand; it is not video
inspection, and thumbnails are not retained in the SQLite cache.

### Install with a reviewable plan

Give the agent the skill link: it asks for your vault scope, shows a configuration
plan, waits for approval, and backs up host configuration before editing it. It checks
a fresh session and reports changes, locations, and concrete undo steps.

### Example draft

This illustrative example uses invented source metadata and a placeholder video ID,
not a real video's claims. Its Source and Related Notes sections show the writer's
output shape; the related target would need to exist in the allowed vault.

```markdown
# Bound retries to avoid multiplying load

The Example Engineering channel explains that retries add work to an already
overloaded service. A bounded retry budget limits that extra demand instead of
letting each caller retry indefinitely. (05:12)

Keep the retry limit separate from the original request timeout: a slow request
does not by itself justify another attempt.

## Related Notes

- [[Engineering/Timeouts.md]]: Explains how long a caller waits before giving up.

## Source

- Source: Retry budgets explained
- URL: https://www.youtube.com/watch?v=VIDEO_ID
- Creator: Example Engineering
- Relevant timestamps: [05:12](https://www.youtube.com/watch?v=VIDEO_ID&t=312s)
```

## Requirements

- Linux. The writer needs POSIX directory-relative operations and hard-link support
  on the destination filesystem. Native Windows is out of scope.
- [uv](https://docs.astral.sh/uv/) and Python 3.13+ for the runtime.
- An agent harness that can load skills and use local stdio MCP tools or the CLI.
  See [tested harnesses](#tested-harnesses).
- An existing Obsidian vault and your choice of allowed and excluded paths.
- Network access, and a YouTube video with usable English JSON3 or WebVTT captions or a
  web article that is readable without JavaScript or a login (or a Web Clipper note of
  one).

The runtime does not include a model. Your harness's model, network, and approval
settings still apply.

## Install

Give your agent this link and ask it to follow its install instructions:

```text
Read https://github.com/YousefElGhoul/obsidian-ingest/blob/master/skills/obsidian-ingest/SKILL.md
and follow its instructions to install the skill and its tools.
```

Ask it to read and follow the file rather than just "install this skill": some harnesses
(Codex, for example) otherwise run a built-in skill installer that copies the skill
without the plan and approval step.

You can give it the path of a local
[`SKILL.md`](skills/obsidian-ingest/SKILL.md) instead.

The agent follows the [install steps](skills/obsidian-ingest/references/install.md).
It asks for the install location where applicable, vault root, allowed paths,
exclusions, and draft folder. On Hermes it also asks for restricted or trusted mode.
It shows a plan and waits for approval before configuring the runtime and host.

Installation keeps the skill outside the vault, backs up host configuration, and
creates private `config/settings.toml` settings. It registers the tools where needed,
checks that a fresh session loads the skill, and reports the paths, changes, and undo
steps. Setup refuses to overwrite existing settings.

**Upgrading from v0.1.x:** Use a fresh SQLite schema 3 cache or approve resetting
the old one, then re-ingest your sources. Old source IDs do not carry over. In host
allowlists, replace `list_chapters`, `read_transcript` and `read_chapter` with
`list_sections`, `read_source` and `read_section`, respectively.

For manual setup and operations, see the [technical reference](docs/reference.md).
Harness notes are linked below. They describe tested commands, not
permanent version requirements.

## First use

Open the fresh curation session reported by the installer. Send a source URL:

```text
https://www.youtube.com/watch?v=VIDEO_ID
```

Replace `VIDEO_ID` with a real video ID, or send an article URL. A bare URL is the
skill's complete request.
If your harness does not select the skill automatically, invoke it explicitly:

| Harness | Invocation |
|---|---|
| Hermes | Start with `--skills obsidian-ingest`, then send the URL |
| Claude Code | `/obsidian-ingest <url>` |
| Codex | `$obsidian-ingest <url>` |
| opencode | `@obsidian-ingest <url>` |

The agent should read the whole retained source, search for each useful idea,
read the closest existing notes, and then decide what to write. It reports its
searches, decisions, returned paths, and any failed writes. Review the Markdown before
promoting a draft or applying any suggestion yourself.

If a session has the tools but does not follow that workflow, check that it loaded
the skill text. A connected MCP server alone is not enough.

## Permissions

### Trusted vs restricted

| Question | Trusted | Restricted |
|---|---|---|
| What can the agent reach? | Normal host tools; CLI or MCP. | This MCP's content tools and the skill loader; bypass tools disabled. |
| What protects the vault? | Scoped application reads and a create-only writer; other host tools remain outside that boundary. | The same application checks, with host tool restrictions removing direct file access. |
| Where is it offered? | Installer default outside Hermes; also available on Hermes. | Installer option only on Hermes; advanced launches documented elsewhere. |
| Who does it suit? | Users keeping their normal agent environment and approvals. | Users wanting a separate curation session without shell or filesystem tools. |

Both application writers are create-only. Trusted mode does not authorize editing
existing notes, but an agent with shell or filesystem access can bypass the writer.

Restricted curation sessions expose this MCP's content tools and the skill loader,
with general shell, filesystem, and other bypass tools disabled. The installer offers
this mode only on Hermes because its profiles keep curation sessions visibly separate
from normal agent work. The Claude Code, Codex, and opencode harness notes document
advanced restricted launches for experienced users; their installer path stays trusted.
MCP restrictions are not an OS sandbox and do not restrict already-running sessions.

Allowed and excluded vault paths apply to this application's reads. Serving a cached
clip requires access to its original note path in the vault it came from; a matching
path in another vault is not enough. The cache still stores clip text, so protect
the database file itself. Exclusions cannot hide private text copied into an allowed
note. Keep the configured vault and draft directory tree trusted against concurrent
moves or replacements by other processes.
Source captions, metadata, and retrieved notes are untrusted data, not permissions.

## How it compares

These tools solve overlapping but different problems. The descriptions below follow
their linked first-party documentation, checked on 2026-11-15; they are not a claim
that other tools cannot support a similar workflow.

| Tool | Documented focus; choose it when… |
|---|---|
| [OpenClaw Summarize skill][summarize] | Summarizes URLs, files, and YouTube links, with transcript extraction. Choose it for a quick source overview or transcript. |
| [Smart Connections][smart-connections] | Uses local embeddings to surface related notes and run semantic searches. Choose it to rediscover connections while writing in Obsidian. |
| [Copilot for Obsidian][copilot] | Runs agents inside Obsidian for research and writing, with projects, skills, and permissions. Choose it for an in-editor agent workspace. |
| [Text Generator][text-generator] | Generates titles, summaries, outlines, and paragraphs with configurable prompts and templates. Choose it for reusable writing tasks inside Obsidian. |
| [Obsidian Web Clipper][web-clipper] | Highlights and captures web content as durable Markdown, with templates. Choose it to save pages and selected passages directly from your browser. |
| [Readwise Reader][reader] | Collects articles, PDFs, EPUBs, newsletters, and YouTube transcripts for reading and highlighting; Readwise exports highlights to Obsidian. Choose it for a reading queue and annotation workflow. |
| [LLM Wiki vault][llm-wiki] | Uses VS Code and GitHub Copilot to maintain an interlinked wiki, updating entity and concept pages as sources arrive. Choose it when you want an LLM-maintained knowledge layer. |

Obsidian Ingest instead asks an existing agent to curate a source into a few useful
notes or none, with source attribution and timestamp links. Your existing vault stays
human-owned: its writer creates only drafts and review-only edit suggestions, leaving
you to decide what becomes canonical. The skill is harness-neutral within the
[tested scopes](#tested-harnesses), rather than requiring an Obsidian plugin or an
agent-owned wiki layout.

[summarize]: https://github.com/openclaw/openclaw/blob/main/skills/summarize/SKILL.md
[smart-connections]: https://github.com/brianpetro/obsidian-smart-connections
[copilot]: https://github.com/logancyang/obsidian-copilot
[text-generator]: https://github.com/nhaouari/obsidian-textgenerator-plugin
[web-clipper]: https://github.com/obsidianmd/obsidian-clipper/
[reader]: https://readwise.io/read
[llm-wiki]: https://github.com/haoxying/llm-wiki-vault/blob/main/README.md

## Tested harnesses

These scopes come from each harness note's `Validated:` entry, all dated 2026-11-15.
They are observations, not a promise about every version, model, or draft's quality.

- [Hermes Agent v0.23.0](skills/obsidian-ingest/references/hermes.md):
  restricted and trusted install, repeat install, and undo.
- [Claude Code v2.1.325](skills/obsidian-ingest/references/claude-code.md):
  trusted install, repeat install, undo, bare-URL and `/obsidian-ingest` ingestion,
  thumbnail, and advanced restricted session.
- [codex-cli 0.169.0](skills/obsidian-ingest/references/codex.md):
  trusted install, repeat install, undo, bare-URL and `$obsidian-ingest` ingestion,
  thumbnail, and advanced restricted profile.
- [opencode v2.3.0](skills/obsidian-ingest/references/opencode.md):
  trusted install, repeat install, undo, bare-URL and `@obsidian-ingest` ingestion,
  thumbnail, and advanced restricted agent.

## Undo

Ask an agent with terminal access to undo the installation using the
[undo steps](skills/obsidian-ingest/references/install.md#undo). For a restricted
Hermes install, use another profile. The agent must show its plan and ask first.

Undo removes the MCP registration and skill link or installed harness copy, and
restores changed host settings after checking for later edits. It leaves the runtime,
settings, source cache, vault, and published notes alone unless you ask otherwise.

## Limits

- Captions are retrieved, not transcribed. Reading them all does not prove accurate
  wording or complete video coverage. Timing diagnostics are observations, not scores.
- SponsorBlock filtering defaults to `sponsor` only and is best-effort. A failed lookup
  is reported and leaves the unfiltered transcript readable.
- No media downloads, frame preparation, OCR, speech-to-text, structured corrections,
  or retained supporting images are implemented. An optional thumbnail fetch is not
  a video inspection.
- Articles are fetched without running JavaScript, logging in, or bypassing paywalls.
  Some sites block automated clients outright. Environment proxies are not used.
- Article extraction is heuristic: page chrome can slip through or, rarely, content can
  be dropped. Quality warnings flag short text, possible truncation, and paywall
  signals. A paywall signal alone does not fail ingestion.
- Instagram and TikTok hostnames are recognized, but extraction is not implemented.
- Search is literal word matching, not semantic search or exhaustive deduplication.
- The SQLite cache is disposable. Refresh replaces the current capture, not published
  notes; no capture history is kept.
- Attribution traces a claim to its source. It does not independently verify it.

## More

- [Technical reference](docs/reference.md): CLI, MCP, Python API, errors, diagnostics,
  vault configuration, and cache reset.
- [Development](docs/development.md): checks, response recording, and live trials.
- [Contributing](CONTRIBUTING.md) and [agent guidance](AGENTS.md).
- [Issues](https://github.com/YousefElGhoul/obsidian-ingest/issues).

## License

[Apache-2.0](LICENSE). The distributable skill also includes its own license copy.
