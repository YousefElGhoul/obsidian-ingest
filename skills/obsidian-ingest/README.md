# Obsidian Ingest skill

This directory is the self-contained Agent Skill root. It includes the workflow,
Python runtime, stdio MCP entry point, and trusted configuration template.

Install the skill directory from the repository with the skills CLI (choose the
target harness during installation):

```bash
npx skills add <owner>/obsidian-ingest --skill obsidian-ingest
```

## Setup

Requires network access for YouTube extraction. For Hermes, first ask the user for
the named profile and explicit vault root, allowed paths, exclusions, and draft folder.
Then run `scripts/install-hermes.sh` with those values. It installs `uv` and Python
3.13 user-locally if needed and configures only the selected Hermes 0.22.5 CLI/TUI
profile. See [the Hermes setup guide](references/hermes.md) for repeat install and
restore instructions.

For standalone use with another harness, install Python 3.13+ and [uv](https://docs.astral.sh/uv/),
then run from this directory:

```bash
uv sync
obsidian-ingest setup
```

The setup prompts for the vault root, allowed paths, exclusions, and draft folder. It
validates the scope, creates `config/settings.toml` with private file permissions, and
never overwrites an existing config. Use `obsidian-ingest setup --config PATH` to put
the settings elsewhere. The server refuses to guess a vault or broaden permissions.

Configure a non-Hermes harness to launch `mcp/server.py` using `uv run` from this
skill directory. Expose only this server's tools to the ingestion profile; MCP
restrictions do not sandbox other host tools.

The `obsidian-ingest` CLI offers the same individual operations for users who
intentionally run commands with their ordinary host permissions. See `SKILL.md`.

This skill currently extracts YouTube metadata and existing English captions. It
does not transcribe audio, run an agent, or support Instagram/TikTok extraction.
