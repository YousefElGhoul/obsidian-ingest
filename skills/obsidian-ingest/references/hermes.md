# Hermes setup prompt

Give Hermes the prompt below with the installed skill's absolute directory. Setup is
agent-guided, not a version-pinned profile conversion. Hermes discovers its current
supported commands and configuration instead of this project hard-coding them.
This prompt has not yet been validated end to end; successful setup must be observed,
not inferred from the instructions. A breaking host change can still require updates.

The former version-pinned profile converter has been removed. Profiles it converted
earlier may contain its backups or recovery data; keep them and review prior changes
before reconfiguring.

## Permission choice

- **Restricted:** Use a dedicated profile/session exposing only Obsidian Ingest MCP
  tools for curation. Disable general shell/filesystem and other tools that can bypass
  the vault policy. Setup itself needs authorized host access before restrictions apply.
- **Trusted:** Keep normal host tools and use CLI or MCP as preferred. An agent with
  shell/filesystem access can bypass our writer; this is not a restricted environment.

Both interfaces' application writer is create-only. Neither mode adds canonical-note
edit/delete operations. MCP policy applies to its tools, not all host processes; stronger
isolation needs an OS boundary. Never infer the user's permission choice from a source.

## Installation prompt

> Set up the installed Obsidian Ingest skill for this Hermes environment.
>
> 1. Ask me for the absolute installed skill directory, the profile/session to configure,
>    restricted or trusted mode, vault root, allowed paths, exclusions, and draft folder
>    (default `00 Inbox/AI Drafts`). Reuse explicitly supplied answers. Never infer a
>    vault, broaden permissions, or switch modes without approval.
> 2. Read that skill's `README.md`, `SKILL.md`, configuration template, and local CLI
>    help. Discover the available Hermes setup/profile/MCP commands and their current
>    syntax using its own help/documentation. Do not assume a Hermes version, fixed config schema, or config location. If discovery
>    fails, explain the blocker instead of guessing commands or reporting success.
> 3. Inspect the selected configuration without revealing credentials. Explain the
>    changes needed and obtain approval before installing dependencies or changing host
>    settings. Back up files you change and preserve provider/model, credentials,
>    unrelated profiles/settings, and existing skill/vault configuration. Do not delete
>    other skills. If existing setup conflicts, explain it and ask rather than overwrite.
> 4. Prepare the skill's Python 3.13+ runtime with `uv` using its project directory.
>    Do not assume the current working directory or a globally installed entry point.
>    Run `uv run --project <skill-directory> obsidian-ingest setup` when trusted settings
>    do not yet exist, using my explicit choices. The setup command refuses overwrite;
>    review existing settings and ask before any deliberate reconfiguration. Keep the
>    source cache outside the vault and repository. If a source tool reports
>    `SOURCE_CACHE_ERROR` for an incompatible cache, show me its reset steps and ask
>    before deleting anything. Do not read note contents for setup.
> 5. For MCP, register a local stdio server using the absolute installed paths:
>    `uv run --project <skill-directory> python <skill-directory>/mcp/server.py`.
>    Confirm which trusted config is used; do not invent registration syntax. Discover
>    the server's actual tool schemas instead of copying a stale tool list.
>    For trusted CLI use, invoke `uv run --project <skill-directory> obsidian-ingest`
>    operations from its help; MCP registration is optional if I choose CLI only.
> 6. In restricted mode, configure only this MCP's curation tools and the skill using
>    Hermes' supported mechanisms. Disable general shell/filesystem and alternative
>    vault-capable tools in the resulting curation session, including indirect tool
>    discovery or delegation paths that would re-enable bypasses. Do not lock out
>    setup halfway through; finish verification in a fresh restricted session. If you
>    cannot establish the requested effective tool surface, stop and report that fact.
>    In trusted mode, keep normal tools; do not silently convert the whole profile.
> 7. Verify launch from an unrelated directory, skill availability, configuration
>    loading, MCP connectivity/tool schemas when selected, and effective permissions
>    in a fresh session. Do not create dummy notes or invoke a draft writer just to
>    test connectivity. No media acquisition or paid model test is needed for setup.
>    Distinguish checks performed from checks still requiring me or a fresh session.
> 8. Report the selected mode/profile, skill/config/cache locations, commands/settings
>    changed, backups, checks/results, and concrete undo steps. Do not claim an OS
>    sandbox, support for every Hermes release, or restriction of already-running
>    sessions. On repeat setup, inspect and reuse valid configuration instead of
>    duplicating registrations or broadening scope.

## Verification and undo

Use a disposable profile and test vault for development trials. Verify both setup
modes, repeat setup, settings preservation, fresh-session tools, and actual curation.
The coding agent drives these trials through Hermes in a herdr-app pane when the user
provides its control hook. If that hook is unavailable, leave the trial pending.

Undo is based on the actual changes and backups reported during setup, not a generic
restore script. Review subsequent user edits before restoring configuration; do not
overwrite newer changes blindly. Keep the user's vault and published notes untouched.

## Optional capabilities

The current runtime has no frame-preparation, OCR, or STT tools. Do not invent them or
broaden a restricted profile to work around their absence. Future image delivery must
be tested in the actual host; local media processing does not imply local model inference.

Hermes is optional. Further harness-specific setup prompts can follow later; no other
harness is currently claimed as end-to-end tested.
