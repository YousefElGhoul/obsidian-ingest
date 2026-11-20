# Hermes notes

Modes: restricted and trusted (offer both; Hermes profiles keep curation sessions
visibly separate).
Validated: Hermes Agent v0.23.0, 2026-11-15 (both modes: install, repeat install, undo).

Hermes-specific details for [the install steps](install.md). They were observed
working on Hermes Agent v0.23.0 in a live install trial. They are hints, not a
version pin: confirm each command with its `--help` before use, and if one is gone,
find the current equivalent in Hermes' own help or documentation rather than in its
source code.

The former version-pinned profile converter has been removed. Profiles it converted
earlier may contain its backups; keep them and review earlier changes before
reconfiguring.

## Profiles and skills

- Every command takes `-p <profile>`. The profile's directory is
  `~/.hermes/profiles/<profile>`; `hermes -p <profile> config path` prints its config.
- Make the skill visible with a symlink:
  `ln -s <skill> ~/.hermes/profiles/<profile>/skills/obsidian-ingest`.

## Register the MCP server

```bash
hermes -p <profile> mcp add obsidian-ingest --command <absolute uv> --connect-timeout 60 \
  --args run --no-dev --project <skill> python <skill>/mcp/server.py \
  --config <skill>/config/settings.toml --cache-db <cache file>
```

`mcp add` asks for confirmation on stdin; pipe `y` into it (`printf 'y\n' | hermes …`). Then limit the server's tools:

```bash
hermes -p <profile> config set mcp_servers.obsidian-ingest.tools \
  '{"include":["ingest_source","get_source_overview","list_sections","list_exclusions","read_source","read_section","get_thumbnail","create_draft","search_vault","read_vault_note","create_edit_suggestion"],"resources":false,"prompts":false}'
hermes -p <profile> mcp test obsidian-ingest
```

Check the result before reporting: the server's entry in `config.yaml` must have the
`--cache-db` argument, the eleven-tool `include` list, `resources: false` and
`prompts: false`, and `mcp test` must list exactly those eleven tools. Fix any
difference first; a "registered 15 tool(s)" line means resources or prompts are still on.

## Restrict (restricted mode)

All settings below live in the profile's `config.yaml`; set them with
`hermes -p <profile> config set <key> <value>`.

- **Do not put `skills` in `agent.disabled_toolsets`.** Without it Hermes never injects
  `SKILL.md` into the curation agent (see the next bullets).
- `agent.disabled_toolsets`: list every built-in toolset except `skills` and the MCP
  one. In the trial this covered `browser`, `clarify`, `code_execution`, `computer_use`,
  `connections`, `cronjob`, `delegation`, `file`, `kanban`, `memory`, `project`,
  `search`, `session_search`, `terminal`, `todo`, `vision`, `web`, the media
  toolsets, and the messaging-platform toolsets. List the toolsets with
  `hermes tools --help` and the tools commands it names; `kanban` matters because it can
  route work to other profiles.
- `mcp_servers.<name>.enabled false` for every other MCP server.
- Keep the `skills` toolset enabled and set `skills.write_approval true`. Hermes injects
  auto-loaded skills only into agents that have a skills tool; without it the curation
  agent never sees `SKILL.md`, even though the banner says "Activated skills".
  `write_approval` stages any skill edit for the user's review.
- `skills.auto_load: ["obsidian-ingest"]`, and every other installed skill in
  `skills.disabled`. Hermes always keeps its own `hermes-agent` skill in the catalog;
  it is not auto-loaded during curation. Report this.
- `tools.tool_search.enabled off`, passed exactly like that. A JSON-quoted `'"off"'`
  is stored as a quoted string and leaves tool search on; check with `config get`.
- `platform_toolsets.cli '["obsidian-ingest","skills"]'`, so CLI sessions start with only this
  toolset.

Start curation sessions with:

```bash
hermes -p <profile> chat --cli --in <unrelated directory> \
  --toolsets obsidian-ingest,skills --skills obsidian-ingest
```

## Trusted mode

Change nothing in "Restrict". The registered MCP tools stay behind Hermes' tool search
(`tool_search`, `tool_describe`, `tool_call`), and agents found and used them in the
trials. Start sessions with `hermes -p <profile> chat --cli --skills obsidian-ingest`.

## Verify

- `hermes -p <profile> mcp test obsidian-ingest` lists the eleven tools.
- Start the curation command above and type `/exit`. The banner lists the MCP tools and
  `Activated skills: obsidian-ingest`. In the trial the banner counted 14 tools while the
  agent received only the allowed ones (the other four are MCP resource and prompt
  helpers that are disabled). Report such a mismatch rather than probing Hermes'
  internals.
- `hermes -p <profile> tools list` shows `skills` enabled, and `mcp list` shows the other
  servers disabled.
- The skill reaches the model: start a curation session and ask, without tools, what
  you must do before writing a note. The answer must be the skill's step 4 (search the
  vault, read the closest notes). In the trial the session context grew from about 3.2K
  to 5K tokens once the `skills` toolset was on.
  `prompt-size` does not help here: offline it reports zero MCP tools.

## Updating the skill

MCP servers are started by the long-running Hermes gateway and keep running between
sessions. After updating the skill's code, stop the old server processes (those running
`<skill>/mcp/server.py`) so the next session starts them with the new code.
Hermes also caches each server's tool schemas in `<profile>/cache/mcp_schema_cache.json`,
keyed by the server's command, arguments and tool list rather than its code. After an
update that changes tool arguments, delete that file and `tool_discovery_cache.json` so
sessions see the new schemas.

Hermes pauses a server after three rejected calls, and calls made while it is paused
extend the pause. The count is shared by every session of one profile. Parallel sessions in
one profile worked in the trials once rejections stopped; if pauses cascade, clone the
profile (`hermes profile create <name> --clone-from <installed profile>`).

## Undo

Run undo from a session with terminal access. A restricted profile has no shell or file
tools, so undo it from another profile (`hermes -p <other> chat`, naming the restricted
profile in the request). Show the planned changes and ask first. Restore the backed-up
`config.yaml` after checking for later edits, then remove only the skill entry in
`~/.hermes/profiles/<profile>/skills/obsidian-ingest`: `unlink` it if it is a symlink,
or move the copy aside if the install copied it. Leave the vault, published notes, the
skill's runtime, settings, and source cache alone unless the user asks.
