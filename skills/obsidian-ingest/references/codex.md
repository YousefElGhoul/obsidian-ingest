# Codex notes

Modes: trusted only. Do not offer restricted mode; mention the advanced restricted
profile below once in the report.
Validated: not yet.

Codex-specific details for [the install steps](install.md). They are hints, not a
version pin: confirm each command with `codex --help` or `codex mcp --help` first.

## Where things live

- The config directory is `$CODEX_HOME`, or `~/.codex` when it is unset. It holds
  `config.toml`, which contains the MCP servers.
- Personal skills load from `~/.agents/skills/<name>/SKILL.md` (based on the home
  directory, not `CODEX_HOME`). `$CODEX_HOME/skills` still works but is deprecated. Make
  the skill visible with a symlink: `ln -s <skill> ~/.agents/skills/obsidian-ingest`.
- Codex also reads project `.codex/`, `.agents/` and `AGENTS.md` from the directory a
  session starts in. Install at user level, not into a project.

## Register the MCP server

Back up `config.toml` first, then:

```bash
codex mcp add obsidian-ingest -- <absolute uv> run --no-dev --project <skill> \
  python <skill>/mcp/server.py --config <skill>/config/settings.toml \
  --cache-db <cache file>
```

Then limit the server to the eleven tools: set `enabled_tools` (the list from
install.md step 5) under `[mcp_servers.obsidian-ingest]` in `config.toml`. Check with
`codex mcp get obsidian-ingest` and `codex mcp list`.

## Approvals

Depending on the user's approval settings, Codex may ask before each MCP tool call. If
it does, offer to set `default_tools_approval_mode` for this server only, so every
transcript page doesn't prompt. Do not change the user's global approval or sandbox
settings.

## Start a session

Start `codex` in any directory and paste a video URL, or run `codex exec "<url>"`.

## Verify

- `codex mcp get obsidian-ingest` shows the server and its enabled tools.
- In a fresh session from an unrelated directory, ask what the obsidian-ingest skill
  requires before writing a note. The answer must be the skill's step 4.

## Undo

Show the planned changes and ask first. Then:
- run `codex mcp remove obsidian-ingest`, or restore `config.toml` from its backup after
  checking for later edits;
- `unlink ~/.agents/skills/obsidian-ingest`;
- delete `$CODEX_HOME/obsidian-ingest.config.toml` if the advanced profile was added.

Leave the vault, notes, the skill's runtime, settings, and source cache alone unless the
user asks.

## Advanced: restricted profile (experienced users)

Codex profiles are files named `$CODEX_HOME/<name>.config.toml`, layered on top of
`config.toml` and selected with `--profile`. A profile that keeps only this MCP server
and turns off the shell tool:

```toml
# $CODEX_HOME/obsidian-ingest.config.toml
sandbox_mode = "read-only"
web_search = "disabled"

[features]
shell_tool = false
unified_exec = false
```

Start it with `codex --profile obsidian-ingest`, then paste a URL. Set `enabled = false`
for any other `[mcp_servers.*]` in the same file. The read-only sandbox is an OS sandbox
for shell commands only; MCP servers run outside it.
