# Installing Obsidian Ingest

Instructions for an agent asked to install this skill and its tools, usually given
only a link to this skill's `SKILL.md`. Follow the steps in order. Ask the user only for
the choices in step 1; everything else is discovered or fixed below.

## 1. Identify the harness, then ask for the user's choices, once

First name the harness you are running in. You normally know it; environment variables
confirm it: `HERMES_HOME` (Hermes), `CLAUDECODE` (Claude Code; `AI_AGENT` alone is not specific),
`CODEX_THREAD_ID` or `CODEX_HOME` (Codex), any variable starting with `OPENCODE`
(opencode). Then read its notes file and follow it
alongside these steps:

| Harness | Notes |
|---|---|
| Hermes | [hermes.md](hermes.md) |
| Claude Code | [claude-code.md](claude-code.md) |
| Codex | [codex.md](codex.md) |
| opencode | [opencode.md](opencode.md) |

Each notes file starts with a `Modes:` line. Offer **restricted** mode only when it says
restricted is supported; otherwise install **trusted** without asking about mode, and
mention any advanced restricted setup the notes file documents once in the report. For
a harness with no notes file, install trusted with that harness's own documented
commands for registering a stdio MCP server and enabling a skill, and say in the report
that this harness is untested and restricted mode is not offered.

Before asking anything, check for an existing install: the skill link, the MCP
registration, and `<skill>/config/settings.toml`. If one exists, read it, show it, and
ask only about what is missing or wrong; on a repeat install, valid settings are the
user's earlier answers. Report where each piece lives, and say if the skill link is in
a location the harness notes call deprecated.

Then ask in a single message, and reuse any answer the user already gave:

- Where to install, if the harness has a choice (on Hermes, a named profile).
- The mode, only where restricted is offered:
  - Restricted: curation sessions expose only this skill's MCP tools; shell,
    filesystem, delegation, and other vault-capable tools are disabled.
  - Trusted: normal host tools stay on, and the agent may use the CLI or MCP.
  Neither mode is an OS sandbox, and both writers are create-only.
- Absolute vault root, allowed vault paths (`*` for all), excluded paths, and draft
  folder (default `00 Inbox/AI Drafts`). An excluded path must not also be listed as
  allowed; to hide part of an allowed folder, exclude a subfolder of it.

Never infer a vault, broaden scope, or switch mode without the user's approval. If the
host's sandbox blocks a command this install needs, such as `uv sync` needing network,
ask the user to approve it. Never copy caches or other files from outside the paths
this install uses as a workaround.

## 2. Decide where the skill will live

Only decide here. Do not clone, update, or link anything until the plan in step 3 is
approved. Do not use a harness's built-in skill installer (such as Codex's
skill-installer): it copies the skill without a plan and skips these steps. The skill directory must stay writable: it holds `config/settings.toml` and
`.venv`.

- From a GitHub link such as
  `https://github.com/<owner>/<repo>/blob/<branch>/skills/obsidian-ingest/SKILL.md`,
  plan to clone the repository once into a stable location outside the vault and
  outside any temporary directory, for example
  `git clone --depth 1 --branch <branch> https://github.com/<owner>/<repo> ~/.local/share/obsidian-ingest/<repo>`.
  The skill directory is `<clone>/skills/obsidian-ingest`.
- If a skill directory already exists (a local path, a previous install, or the skills
  CLI `npx skills add <owner>/<repo> --skill obsidian-ingest`), reuse it. Update a
  clone with `git -C <clone> pull --ff-only`; trusted settings are gitignored and
  survive that. A skills-CLI copy is replaced on update, which removes
  `config/settings.toml` and `.venv` kept inside it: back up the settings first and
  run setup again afterwards.

The skill must also be visible to the harness. Prefer a symlink from the harness's
skills directory to the skill directory over copying, so updates and settings stay in
one place. If a different skill of the same name already exists there, stop and ask.

## 3. Show the plan, get approval, back up

Before changing anything, list every change with its path (the clone or update and the
skill link from step 2, and steps 4 to 6) and ask for approval in one message, even if the user already gave every choice or said "go
ahead". Then end your turn and wait for the answer: never show the plan and start
changing things in the same turn. After approval, back up every host configuration
file you will edit (for example `config.yaml.before-obsidian-ingest-<timestamp>`), then
clone or update the skill and create the link as decided in step 2. Preserve provider,
model, credentials, other profiles, and other skills. Do not print credentials.

## 4. Prepare the runtime and settings

With `<skill>` as the absolute skill directory:

```bash
uv sync --project <skill> --locked --no-dev
uv run --no-dev --project <skill> obsidian-ingest setup \
  --vault-root <vault> --allow <path> [--allow <path> ...] \
  [--exclude <path> ...] [--draft-folder <folder>]
```

Always pass `--no-dev` to `uv run`; otherwise uv installs the development tools on
every launch. `setup` with flags never prompts, validates the scope against the vault,
writes `<skill>/config/settings.toml` with mode `0600`, and refuses to overwrite an
existing file. If settings already exist, show them and ask before any reconfiguration.
Do not read note contents during setup.

Choose a source cache file outside the vault and the repository that belongs to this
install alone, for example `~/.local/state/obsidian-ingest/<profile or install
name>/sources.sqlite3`. Never point a new install at a cache file that already exists:
it may come from an older version and fail with `SOURCE_CACHE_ERROR`. If a source tool
later reports `SOURCE_CACHE_ERROR`, show its reset steps and ask before deleting
anything. Likewise give the skill copy its own folder (for example
`~/.local/share/obsidian-ingest/<profile or install name>`).

## 5. Register the MCP server

Register one local stdio server named `obsidian-ingest`, with absolute paths:

```text
command: <absolute path to uv>
args:    run --no-dev --project <skill> python <skill>/mcp/server.py
         --config <skill>/config/settings.toml [--cache-db <cache file>]
```

Allow exactly these eleven tools: `ingest_source`, `get_source_overview`,
`list_sections`, `list_exclusions`, `read_source`, `read_section`, `get_thumbnail`,
`create_draft`, `search_vault`, `read_vault_note`, `create_edit_suggestion`. Disable
MCP resources and prompts. If the server reports a different tool list, use what it
reports and tell the user. In trusted CLI-only mode, registration is optional: use
`uv run --no-dev --project <skill> obsidian-ingest --help`.

## 6. Restrict (restricted mode only; skip in trusted mode)

Use the harness's supported settings, not its internal source code.

**Never disable the harness's skills toolset or skill loader** (on Hermes, the `skills`
toolset). Many harnesses put a skill into the agent's context only when that toolset is
on; without it the agent gets the MCP tools but never sees `SKILL.md`, while the banner
still says the skill is active. If the toolset can also edit skills, require the user's
approval for skill edits (on Hermes, `skills.write_approval true`) instead of
disabling it.

Then:

- Disable shell, file, code execution, browser, web, computer use, delegation or
  subagents, scheduling, cross-profile task boards, connector management, and tool
  search or discovery features that could re-enable tools.
- Disable the profile's other MCP servers. Keep their registrations.
- Load only this skill in curation sessions, and disable other note-taking or
  knowledge-base skills (for example `obsidian`, `personal-knowledge`, `llm-wiki`).
- Make curation sessions start with exactly two toolsets: this MCP server's and the
  skills toolset.

Do not lock yourself out mid-setup: change the curation profile's settings, but run the
final check in a fresh session. In trusted mode, change nothing in this step.

## 7. Verify with the harness's own commands

- The MCP server connects and lists the eleven tools (on Hermes: `mcp test`).
- A fresh session started from an unrelated directory loads the skill and exposes only
  the intended tools. Prefer the harness's own tool listing or banner. If those
  disagree, say so instead of writing scripts against the harness's internals.
- The settings load and the paths resolve as configured.
- The curation agent has the skill text: in a fresh session, ask without tools what it
  must do before writing a note. The answer must be this skill's step 4 (search the
  vault and read the closest notes). A banner saying the skill is active is not proof.

Do not create test notes, call `create_draft`, or ingest a source to test connectivity.

## 8. Report

Report the mode and profile, the skill, settings, and cache locations, every command and
setting changed, the backups, each check with its result, the exact command to start a
curation session, and concrete undo steps. Write the undo steps so they work without
this guide: ask the user first; remove only the registration, skill link, and added
permission rules; restore backups only after checking for later edits; keep the
runtime, settings, source cache, and vault unless the user asks to remove them. State what was not verified. Do not claim an
OS sandbox, support for every harness version, or restriction of already-running
sessions. On a repeat install, reuse valid configuration rather than duplicating it.

In restricted mode, say that undo must run from a session with terminal access (for
example another profile), because the restricted profile has no shell or file tools.

## Undo

When asked to undo, read the harness notes file's Undo section, list what you will
change, and ask the user before changing anything, even when the harness would approve
the commands without asking. Then check for config edits made after the install,
remove the MCP registration with the harness's own command where it has one, restore
the backed-up host configuration, and remove only the skill link (or the copy the install
placed in the harness's skills directory). Leave the runtime, settings, source cache,
vault, and published notes alone unless the user asks to remove them. Verify against
the backup and report what changed.
