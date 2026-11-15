# Obsidian Ingest skill

This self-contained Agent Skill turns a YouTube URL into a few new drafts,
review-only edit suggestions, or nothing in your existing Obsidian vault. It includes
the workflow, Python runtime, stdio MCP entry point, and configuration template.

## Setup

Requires Linux, Python 3.13+, [uv](https://docs.astral.sh/uv/), an existing vault,
network access, and an agent harness with skills and CLI or local stdio MCP support.
Give an agent this directory's [SKILL.md](SKILL.md) path or link and ask it to install
the skill and its tools. It follows [references/install.md](references/install.md),
asks for vault scope, shows a plan, waits for approval, and backs up changed host config.

Harness notes record tested versions and scopes, not permanent version requirements:
[Hermes](references/hermes.md), [Claude Code](references/claude-code.md),
[Codex](references/codex.md), and [opencode](references/opencode.md).
The installer offers restricted mode on Hermes only; elsewhere it installs trusted
and points experienced users to advanced restricted launches.

For manual runtime setup, run from this directory (omit setup flags to be prompted):

```bash
uv sync --locked --no-dev
uv run --no-dev obsidian-ingest setup --vault-root /path/to/vault \
  --allow Notes --allow '00 Inbox/AI Drafts'
```

The setup prompts for the vault root, allowed paths, exclusions, and draft folder. It
validates the scope, creates `config/settings.toml` with private file permissions, and
never overwrites an existing config. Use `obsidian-ingest setup --config PATH` to put
the settings elsewhere. The server refuses to guess a vault or broaden permissions.

For MCP, configure the host to launch `mcp/server.py` using `uv run --no-dev` from this
directory or absolute paths, as described in the install steps.

## Use and boundaries

In the installer's fresh curation session, send a bare YouTube URL. Explicit invocation
is `/obsidian-ingest <url>` on Claude Code, `$obsidian-ingest <url>` on Codex, or
`@obsidian-ingest <url>` on opencode. Hermes uses `--skills obsidian-ingest` in the
session command. Review the created drafts, by default under `00 Inbox/AI Drafts/`.

Both CLI and MCP writers are create-only; they never modify an existing note.
Trusted mode keeps normal host tools, which can bypass that boundary. Restricted mode
disables those bypass tools for curation sessions. Neither mode is an OS sandbox.

The `obsidian-ingest` CLI exposes the same operations as MCP for users who
intentionally run commands with their ordinary host permissions; only identifiers and
envelopes differ. See [SKILL.md](SKILL.md) for the workflow and
[references/details.md](references/details.md) for evidence limits, errors, and
transport differences.

This skill extracts YouTube metadata and existing English JSON3 or VTT captions. It
does not download media, prepare frames, run OCR/STT, record corrections, run an agent,
or support Instagram/TikTok extraction. The alpha target is Linux and English-caption
YouTube. Captions and source attribution do not independently verify a claim. The
source cache is disposable, not a historical evidence archive.

To undo, ask an agent with terminal access to follow
[the undo steps](references/install.md#undo). It asks first, removes the registration
and skill link, and restores changed host settings after checking for later edits.
Runtime, settings, cache, vault, and published notes stay unless you ask otherwise.

Licensed under [Apache-2.0](LICENSE).
