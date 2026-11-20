# Codex notes

Modes: trusted only. Do not offer restricted mode; mention the advanced restricted
profile below once in the report.
Validated: codex-cli 0.169.0, 2026-11-15 (trusted install, repeat install, undo,
bare-URL and `$obsidian-ingest` ingestion, thumbnail, advanced restricted profile).

Codex-specific details for [the install steps](install.md). They are hints, not a
version pin: confirm each command with `codex --help` or `codex mcp --help` first.

## Where things live

- The config directory is `$CODEX_HOME`, or `~/.codex` when it is unset. It holds
  `config.toml`, which contains the MCP servers.
- Personal skills load from `~/.agents/skills/<name>/SKILL.md` (based on the home
  directory, not `CODEX_HOME`). `$CODEX_HOME/skills` still works but is deprecated: do
  not use it. Make the skill visible with a symlink:
  `ln -s <skill> ~/.agents/skills/obsidian-ingest`.
- Do not use Codex's built-in skill-installer for this skill: it copies the skill into
  `$CODEX_HOME/skills` without a plan. Clone and link it as the install steps say.
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
it does, offer to set `default_tools_approval_mode = "approve"` under
`[mcp_servers.obsidian-ingest]`, so every transcript page doesn't prompt. The only valid
values are `auto`, `prompt`, `writes` and `approve`. Any other value makes Codex refuse
to load its config, which stops the session, so run `codex mcp get obsidian-ingest`
after every edit to confirm the config still loads. Do not change the user's global
approval or sandbox settings.

Codex's sandbox usually blocks network for shell commands, so `uv sync` may fail. Ask
the user to approve that command with network access, and do not work around it.
Checks that need the network or the MCP server, such as a fresh `codex exec`, may also
fail inside the sandbox. Ask to run them with approval, or give the user the command to
run.

Codex has no per-server switch for MCP resources and prompts; say so in the report.

## Start a session

Start `codex` in any directory and type `$obsidian-ingest <url>`. Codex invokes skills
by `$name`, not with a slash. A pasted bare URL usually selects the skill too. Tell the
user both in the report.

## Verify

- `codex mcp get obsidian-ingest` shows the server and its enabled tools.
- In a fresh session from an unrelated directory, ask what the obsidian-ingest skill
  requires before writing a note. The answer must be the skill's step 4.

## Undo

Show the planned changes with their paths, and what stays, and ask first. Then:
- run `codex mcp remove obsidian-ingest`, or restore `config.toml` from its backup after
  checking for later edits;
- `unlink ~/.agents/skills/obsidian-ingest`;
- delete `$CODEX_HOME/obsidian-ingest.config.toml` if the advanced profile was added.

Leave the vault, notes, the skill's runtime, settings, and source cache alone unless the
user asks.

## Advanced: restricted profile (experienced users)

Codex profiles are files named `$CODEX_HOME/<name>.config.toml`, layered on top of
`config.toml` and selected with `--profile`. A profile that turns off the shell, web
search, account app connectors, plugins, goals and image generation:

```toml
# $CODEX_HOME/obsidian-ingest.config.toml
sandbox_mode = "read-only"
web_search = "disabled"

[features]
shell_tool = false
unified_exec = false
apps = false
plugins = false
goals = false
image_generation = false
```

Start it with `codex --profile obsidian-ingest`, then type `$obsidian-ingest <url>`. The
`$` mention injects the skill text, so no file tool is needed. Set `enabled = false` for
any other `[mcp_servers.*]` in the same file. Do not turn off `code_mode_host`: Codex
calls MCP tools through `functions.exec`, so without it the skill's tools are
unreachable. The `collaboration.*` sub-agent tools and `apply_patch` stay available
and cannot be turned off here. Under the read-only sandbox, a patch stops at an
approval prompt: decline it. To check, ask the session to list its tools by name. The read-only sandbox is
an OS sandbox for shell commands only; MCP servers run outside it.
