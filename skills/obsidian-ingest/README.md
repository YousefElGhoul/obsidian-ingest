# Obsidian Ingest skill

This directory is the self-contained Agent Skill root. It includes the workflow,
Python runtime, stdio MCP entry point, and trusted configuration template.

Install the skill directory from the repository with the skills CLI (choose the
target harness during installation):

```bash
npx skills add <owner>/obsidian-ingest --skill obsidian-ingest
```

## Setup

Requires network access for YouTube extraction. The intended setup is to give an agent
this skill's `SKILL.md` link and ask it to install the skill and its tools; it follows
[references/install.md](references/install.md), with Hermes details in
[references/hermes.md](references/hermes.md). That flow has been validated once on
Hermes v0.23.0 in restricted mode; no other harness is validated end to end.

Manually, install Python 3.13+ and [uv](https://docs.astral.sh/uv/), then run from this
directory (omit the flags to be prompted instead):

```bash
uv sync --no-dev
uv run --no-dev obsidian-ingest setup --vault-root /path/to/vault --allow Notes
```

The setup prompts for the vault root, allowed paths, exclusions, and draft folder. It
validates the scope, creates `config/settings.toml` with private file permissions, and
never overwrites an existing config. Use `obsidian-ingest setup --config PATH` to put
the settings elsewhere. The server refuses to guess a vault or broaden permissions.

For MCP, configure the host to launch `mcp/server.py` using `uv run` from this skill
directory or absolute paths. Choose restricted (MCP-only) or trusted (normal host
tools, CLI or MCP) mode as described in `references/details.md`; MCP is not an OS sandbox.

The `obsidian-ingest` CLI exposes the same operations as MCP for users who
intentionally run commands with their ordinary host permissions; only identifiers and
envelopes differ. See `SKILL.md` for the workflow and `references/details.md` for evidence limits and transport
differences, and the repository README for result, error, cache, and diagnostics details.

This skill extracts YouTube metadata and existing English JSON3 or VTT captions. It
does not download media, prepare frames, run OCR/STT, record corrections, run an agent,
or support Instagram/TikTok extraction. The alpha target is Linux and English-caption
YouTube. The repository's `TODO.md` owns the roadmap, hands-on Hermes trials, and
deferred work.
