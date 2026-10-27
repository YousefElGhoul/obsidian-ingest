# Obsidian Ingest skill

This directory is the self-contained Agent Skill root. It includes the workflow,
Python runtime, stdio MCP entry point, and trusted configuration template.

Install the skill directory from the repository with the skills CLI (choose the
target harness during installation):

```bash
npx skills add <owner>/obsidian-ingest --skill obsidian-ingest
```

## Setup

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and network access for
YouTube extraction. From this directory:

```bash
uv sync
obsidian-ingest setup
```

The setup prompts for the vault root, allowed paths, exclusions, and draft folder. It
validates the scope, creates `config/settings.toml` with private file permissions, and
never overwrites an existing config. Use `obsidian-ingest setup --config PATH` to put
the settings elsewhere. The server refuses to guess a vault or broaden permissions.

Configure the chosen agent harness to launch `mcp/server.py` using `uv run` from
this skill directory. Follow the setup guide in `references/hermes.md` for the
first supported dedicated-profile configuration. Expose only this server's tools to
the ingestion profile; MCP restrictions do not sandbox other host tools.

The `obsidian-ingest` CLI offers the same individual operations for users who
intentionally run commands with their ordinary host permissions. See `SKILL.md`.

This skill currently extracts YouTube metadata and existing English captions. It
does not transcribe audio, run an agent, or support Instagram/TikTok extraction.
