# opencode notes

Modes: trusted only. Do not offer restricted mode; mention the advanced restricted
agent below once in the report.
Validated: opencode v2.3.0, 2026-11-15 (trusted install, repeat install, undo, bare-URL
and `@obsidian-ingest` ingestion, thumbnail, advanced restricted agent).

opencode-specific details for [the install steps](install.md). They are hints, not a
version pin: confirm each command with `opencode --help` or `opencode mcp --help` first.

## Where things live

- The config file is `~/.config/opencode/opencode.jsonc` (or `$XDG_CONFIG_HOME/opencode/`).
  `opencode debug paths` prints every location opencode uses.
- opencode reads personal skills from `~/.config/opencode/skills`, and also from
  `~/.agents/skills` and `~/.claude/skills`. Make the skill visible with a symlink:
  `ln -s <skill> ~/.config/opencode/skills/obsidian-ingest`. If a link of that name
  already exists in one of the other two folders, reuse it instead of adding a second.
- opencode also reads project `.opencode/` folders from the directory a session starts
  in. Install at user level, not into a project.
- opencode runs one shared background server per user. Config changes apply to new
  sessions; `opencode reload` reloads them.

## Register the MCP server

Back up `opencode.jsonc` first, then:

```bash
opencode mcp add --global obsidian-ingest -- <absolute uv> run --no-dev \
  --project <skill> python <skill>/mcp/server.py \
  --config <skill>/config/settings.toml --cache-db <cache file>
```

Without `--global`, opencode adds the server to the current project instead. The
result is `mcp.servers.obsidian-ingest` with `"type": "local"` in the global
`opencode.jsonc`. `opencode mcp list` can report "No MCP servers configured" depending
on the directory it runs in, so verify in a session instead (see Verify). Sessions
reach MCP tools through the `execute` tool by default; their permission name is
`obsidian-ingest_<tool>`.

## Permissions

opencode may ask before tool calls, depending on the user's permissions. If it asks
for every transcript page, offer to add this rule to the top-level `permissions` array
of `opencode.jsonc` (create the array if missing):

```jsonc
"permissions": [
  { "action": "obsidian-ingest_*", "resource": "*", "effect": "allow" }
]
```

Back up first, and do not change other permissions.

## Start a session

Start `opencode` in any directory and type `@obsidian-ingest <url>`, which loads the
skill. A pasted bare URL usually loads it too. Tell the user both in the report.

## Verify

- In a new session, the status bar shows the MCP server count (`/mcps` lists
  `obsidian-ingest  Connected`).
- opencode lists only a skill's description until the skill is loaded. Ask what the
  skill requires before writing a note only after loading it (`@obsidian-ingest`). The
  answer must be the skill's steps 2–4.

## Undo

Show the planned changes with their paths, and what stays, and ask first. Then remove
the `obsidian-ingest` entry from `mcp` (and the allow rule, if added), or restore the
backup after checking for later edits, and remove the skill link. Leave the vault,
notes, the skill's runtime, settings, and source cache alone unless the user asks.

## Advanced: restricted agent (experienced users)

opencode permissions can be set per agent. An agent that may only load skills and use
this server's tools, added under `agents` in `opencode.jsonc`:

```jsonc
"agents": {
  "obsidian-ingest": {
    "description": "Curate a video into vault drafts with obsidian-ingest only",
    "mode": "primary",
    "permissions": [
      { "action": "*", "resource": "*", "effect": "deny" },
      { "action": "skill", "resource": "obsidian-ingest", "effect": "allow" },
      { "action": "obsidian-ingest_*", "resource": "*", "effect": "allow" }
    ]
  }
}
```

Start `opencode`, press shift+tab until the footer shows the `obsidian-ingest` agent,
then type `@obsidian-ingest <url>`. The TUI has no `--agent` flag. To check, ask the
session to list its tools by name: expect only `skill` and the `obsidian-ingest_*`
tools. It is not an OS sandbox.
