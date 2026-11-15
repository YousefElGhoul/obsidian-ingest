# Claude Code notes

Modes: trusted only. Do not offer restricted mode; mention the advanced restricted
session below once in the report.
Validated: Claude Code v2.1.325, 2026-11-15 (trusted install, repeat install, undo,
bare-URL and `/obsidian-ingest` ingestion, thumbnail, advanced restricted session).

Claude Code-specific details for [the install steps](install.md). They are hints, not a
version pin: confirm each command with `claude --help` or `claude mcp --help` first.

## Where things live

- The user config directory is `$CLAUDE_CONFIG_DIR`, or `~/.claude` when it is unset.
  MCP servers registered with `--scope user` are stored in the user config, and settings
  live in `<config dir>/settings.json`.
- Personal skills load from `<config dir>/skills/<name>/SKILL.md`. Make the skill
  visible with a symlink: `ln -s <skill> <config dir>/skills/obsidian-ingest`.
- Claude Code also reads project `.claude/` folders and `CLAUDE.md` from the directory
  a session starts in. Install at user scope, not into a project.
- Install into the config directory the session uses. Do not offer to switch to another
  home or config directory.

## Register the MCP server

`claude mcp add` writes `<config dir>/.claude.json` when `CLAUDE_CONFIG_DIR` is set,
otherwise `~/.claude.json`. Back that file up first.

```bash
claude mcp add --scope user obsidian-ingest -- <absolute uv> run --no-dev \
  --project <skill> python <skill>/mcp/server.py \
  --config <skill>/config/settings.toml --cache-db <cache file>
```

Claude Code exposes every tool the server offers; there is no per-server allow-list to
set. Check with `claude mcp get obsidian-ingest` and `claude mcp list`.

## Permissions

Trusted sessions ask before each MCP tool call. In step 1, ask as its own question
whether to add the allow rule `mcp__obsidian-ingest__*` to `permissions.allow` in
`<config dir>/settings.json`, so the user isn't prompted for every transcript page. If
yes, back the file up first. Do not change other permissions. On a repeat install, read
`settings.json` first: if the rule is already there, do not ask again.

## Start a session

Start `claude` in any directory and type `/obsidian-ingest <url>`; autocomplete shows
the `[url]` hint. A pasted bare URL usually loads the skill too. Tell the user both in
the report.

## Verify

Run these checks during the install. `claude -p` starts a new process, so no restart is
needed.

- `claude mcp get obsidian-ingest` shows the server as connected.
- Claude Code shows only a skill's description until the skill is invoked, so asking
  what the skill requires without invoking it cannot pass. Instead, from an unrelated
  directory, run `echo "/obsidian-ingest What must you do before writing a note? Answer
  from the skill text." | claude -p --tools ""` (pass the prompt on stdin: `--tools`
  swallows a following argument). The answer must be the skill's steps 2–4.

## Undo

Show the planned changes and ask first. Then:
- run `claude mcp remove --scope user obsidian-ingest`;
- restore `settings.json` from its backup after checking for later edits, or remove only
  the allow rule this install added;
- `unlink <config dir>/skills/obsidian-ingest`.

Leave the vault, notes, the skill's runtime, settings, and source cache alone unless the
user asks.

## Advanced: restricted session (experienced users)

Claude Code has no profiles, so restricted mode is a launch command rather than an
install option. This alias loads only this MCP server, removes built-in tools other than
the skill loader, and denies anything that would prompt:

```bash
alias obsidian-ingest-session='claude --strict-mcp-config \
  --mcp-config "{\"mcpServers\":{\"obsidian-ingest\":{\"command\":\"<absolute uv>\",\"args\":[\"run\",\"--no-dev\",\"--project\",\"<skill>\",\"python\",\"<skill>/mcp/server.py\",\"--config\",\"<skill>/config/settings.toml\",\"--cache-db\",\"<cache file>\"]}}}" \
  --tools "Skill" --allowedTools "mcp__obsidian-ingest__*" \
  --disallowedTools "<plugin tool>" --permission-mode dontAsk'
```

Tools from Claude Code plugins, including plugins synced from a claude.ai account, stay
available and run in this session; `--strict-mcp-config`, `--tools` and `--restricted`
do not remove them. To find them, start the alias and ask the session to list its tools
by name. Name each tool other than `Skill` and `mcp__obsidian-ingest__*` in
`--disallowedTools` (one quoted name each), then check the list again. Drop that flag
if there are none. It is not an OS sandbox.
