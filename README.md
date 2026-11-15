# Obsidian Ingest

Turn a YouTube URL into a few useful notes for your existing Obsidian vault.
An agent reads the captions, searches your allowed notes, and chooses new drafts,
review-only edit suggestions, or nothing. You review the result.

This is an alpha release: it works on the harnesses and scopes listed under
[tested harnesses](#tested-harnesses), and install and distribution are still being
verified.

This is an Agent Skill with a Python runtime, not a bundled agent or an unattended
pipeline. Python handles source access, provenance, vault scope, and create-only
writes. Your host agent decides what is worth keeping and how to explain it.

## What it does

- Retrieves YouTube metadata, creator chapters, and existing English captions.
- Preserves caption wording, order, and timestamps for the agent to read.
- Searches and reads Markdown notes within the vault scope you approve.
- Creates Markdown drafts in `00 Inbox/AI Drafts/` by default.
- Adds source attribution and timestamp links, plus verified related-note links
  when the agent supplies accessible targets.
- Creates edit suggestions as separate drafts. It never applies them to your notes.
- Reuses a local SQLite source cache across sessions.

It does not replace your vault with an agent-owned wiki. It does not need to write a
note for every video or chapter. Zero notes is a valid result.

The skill keeps ideas worth looking up again months later: techniques, mechanisms,
tradeoffs, gotchas, and mental models. It deliberately writes nothing for let's-plays,
vlogs, how a video game works, or general career and study advice.

## Requirements

- Linux. The writer needs POSIX directory-relative operations and hard-link support
  on the destination filesystem. Native Windows is out of scope.
- [uv](https://docs.astral.sh/uv/) and Python 3.13+ for the runtime.
- An agent harness that can load skills and use local stdio MCP tools or the CLI.
  See [tested harnesses](#tested-harnesses).
- An existing Obsidian vault and your choice of allowed and excluded paths.
- Network access and a YouTube video with usable English JSON3 or WebVTT captions.

The runtime does not include a model. Your harness's model, network, and approval
settings still apply.

## Install

Give your agent this link and ask it to install the skill and its tools:

```text
Install this skill and its tools:
https://github.com/YousefElGhoul/obsidian-ingest/blob/master/skills/obsidian-ingest/SKILL.md
```

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

For manual setup and operations, see the [technical reference](docs/reference.md).
Harness notes are linked below. They describe tested commands, not
permanent version requirements.

## First use

Open the fresh curation session reported by the installer. Send a source URL:

```text
https://www.youtube.com/watch?v=VIDEO_ID
```

Replace `VIDEO_ID` with a real video ID. A bare URL is the skill's complete request.
If your harness does not select the skill automatically, invoke it explicitly:

| Harness | Invocation |
|---|---|
| Hermes | Start with `--skills obsidian-ingest`, then send the URL |
| Claude Code | `/obsidian-ingest <url>` |
| Codex | `$obsidian-ingest <url>` |
| opencode | `@obsidian-ingest <url>` |

The agent should read the whole retained transcript, search for each useful idea,
read the closest existing notes, and then decide what to write. It reports its
searches, decisions, and the paths actually created. Review the Markdown before
promoting a draft or applying any suggestion yourself.

If a session has the tools but does not follow that workflow, check that it loaded
the skill text. A connected MCP server alone is not enough.

## Permissions

**Trusted:** normal host tools remain available, and the agent may use CLI or MCP.
Both application writers are create-only, but an agent with shell or filesystem
access can bypass them. Trusted mode does not authorize editing existing notes.

**Restricted:** curation sessions expose this MCP's content tools and the skill
loader, with general shell, filesystem, and other bypass tools disabled. The installer
offers this mode only on Hermes, whose profiles keep those sessions visibly separate.
Other harness notes describe advanced restricted launches for experienced users.
MCP restrictions are not an OS sandbox and do not restrict already-running sessions.

Allowed and excluded vault paths apply to this application's reads. Exclusions cannot
hide private text copied into an allowed note. Keep the configured vault and draft
directory tree trusted against concurrent moves or replacements by other processes.
Source captions, metadata, and retrieved notes are untrusted data, not permissions.

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
- Instagram and TikTok hostnames are recognized, but extraction is not implemented.
- Search is literal word matching, not semantic search or exhaustive deduplication.
- The SQLite cache is disposable. Refresh replaces the current capture, not published
  notes; no capture history is kept.
- Attribution traces a claim to its source. It does not independently verify it.

## More

- [Technical reference](docs/reference.md): CLI, MCP, Python API, errors, diagnostics,
  vault configuration, and cache reset.
- [Development](docs/development.md): checks, response recording, and live trials.
- [Roadmap](TODO.md) and [contributor guidance](AGENTS.md).
- [Issues](https://github.com/YousefElGhoul/obsidian-ingest/issues).

## License

[Apache-2.0](LICENSE). The distributable skill also includes its own license copy.
