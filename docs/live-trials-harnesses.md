# Live-trial harness notes

Tester notes for the [live-trial protocol](live-trials.md), not user installation
instructions. The observations below come from the 2026-11-15 trials: Claude Code
2.1.325, codex-cli 0.169.0, Hermes 0.23.0, and opencode 2.3.0. Confirm current syntax
with the installed harness's help. Do not treat those versions as pins.

For non-Hermes harnesses, launch through `scripts/trials/sandbox`; it supplies the
isolated environment and auto-approve flag. Never copy these bypass flags into a
real-home launch. `sandbox <harness> exec <command...>` runs a check inside the same
sandbox. Refresh credentials before each run. Save exports outside the child-visible
home before teardown.

## Claude Code

| Item | Tester guidance |
|---|---|
| Launch | `scripts/trials/sandbox claude agent --model <model>` |
| Auto-approve | Wrapper uses `--permission-mode bypassPermissions`, only inside bwrap. |
| Model | `--model <model>` or `-m <model>`; choose separately for install and ingest. |
| Invoke | Ingest with the actual `/obsidian-ingest <url>`; a bare URL is only an optional discovery check. Skills initially expose only their description. Unattended explicit runs: `hc run --bg --name <n> scripts/trials/sandbox claude agent --model <model> -p "/obsidian-ingest <url>"` passes a real slash command (verified 2026-11-21); the watcher reports its exit and `hc read` shows the result. |
| Transcripts | `<CLAUDE_CONFIG_DIR>/projects/*/*.jsonl`; dedupe `tool_use` by `id`. |
| Wait | `scripts/trials/watch` in the background; confirm readiness and completion with `hc read`. |
| Pre-seeding | `prepare` seeds onboarding, theme, work-directory trust, and `skipDangerousModePermissionPrompt`. Without the last setting the bypass warning defaults to “No, exit.” |
| Verify skill | From an unrelated directory, prompt on stdin: `echo "/obsidian-ingest What must you do before writing a note?" \| claude -p --tools ""`. Keep the prompt off the argument following `--tools`, which can swallow it. Run this through the trial sandbox. |
| Traps | MCP schemas are deferred: weaker models sometimes call writes without loading the schema, then recover. Record rejected and corrected calls separately. Watch for a skipped install plan and attempts to Read/Edit a newly created draft. Both violate the workflow even if the final state looks good. |
| Restricted probe | Use the advanced launch in `references/claude-code.md`. Synced plugin tools can leak through `--strict-mcp-config`, `--tools`, and `--restricted`; enumerate them and use `--disallowedTools`. `--restricted` also hid the skill in a trial. `--bare` needed an API key. `dontAsk` plus an allow-list denied install copy/mkdir, so do not use the curation restriction to install. |

Copied logins can expire within hours. A probe unexpectedly showing “API Usage
Billing” must receive no task; refresh credentials first. `/usage` is for the
between-run usage probe, not an ingestion prompt.

## Codex

| Item | Tester guidance |
|---|---|
| Launch | `scripts/trials/sandbox codex agent --model <model>` |
| Auto-approve | Wrapper uses `--dangerously-bypass-approvals-and-sandbox`, only inside bwrap. The outer bwrap boundary remains necessary. |
| Model | `--model <model>` or `-m <model>`. |
| Invoke | Ingest with `$obsidian-ingest <url>` (`hc send` works); the `$name` mention injects `SKILL.md`. A bare URL is only an optional discovery check. |
| Skill path | `~/.agents/skills`; `$CODEX_HOME/skills` is deprecated. |
| Transcripts | `$CODEX_HOME/sessions/**/rollout-*.jsonl`; retain call identifiers when deduplicating. |
| Wait | `scripts/trials/watch`; Codex reports idle briefly mid-turn, which the watcher ignores. Startup can be slow: read the pane before sending. |
| Pre-seeding | `prepare` sets `[projects."<work dir>"] trust_level = "trusted"` in the throwaway `config.toml`. |
| Traps | MCP calls require code mode's `functions.exec`; do not disable `code_mode_host`. Read one transcript page per call: a multi-page script previously truncated output and falsely appeared complete. `default_tools_approval_mode` accepts only `auto`, `prompt`, `writes`, or `approve`; invalid values can prevent every turn from loading config. Watch for installs skipping their plan. |
| Restricted probe | Profiles are `$CODEX_HOME/<name>.config.toml`. The advanced profile cannot remove `collaboration.*` or `apply_patch`. Its read-only policy depends on patch approval: verify that the trial's bypass flag has not overridden it, and report residual tools honestly. |
| Cleanup | `$CODEX_HOME/packages` is Codex's runtime: never delete it or install the skill there. App-server daemons can outlive sessions, with sockets under `/tmp/codex-daemon-<uid>/`; stop only trial-owned PIDs before deleting the trial home. |

Normal Codex shell sandboxing can deny network for `uv sync`. Ask for permission in
a normal launch; never work around it by copying the user's real `~/.cache/uv`.
The outer trial sandbox keeps network on. `/status` shows five-hour and weekly limits.

## Hermes

| Item | Tester guidance |
|---|---|
| Prepare | `scripts/trials/prepare hermes`; profile name comes from `TRIAL_HERMES_PROFILE` (default `oikit`). Abort if profile creation fails. |
| Profile method | Observed command: `hermes profile create <profile> --clone-from default --no-alias`. A clone is not an OS sandbox; inspect inherited config and protect credentials in reports. |
| Launch | `hermes -p <profile> chat --cli --skills obsidian-ingest --in <profile>/cache/scratch`, as an `hc` child. Use the installed skill's restricted curation command when testing that mode: `hermes -p <profile> chat --cli --in <scratch> --toolsets obsidian-ingest,skills --skills obsidian-ingest`. After install, confirm `--cache-db`, the 11-tool include list and `resources`/`prompts: false` (see `references/hermes.md`). |
| Auto-approve | None. Do not add bypass flags outside an OS sandbox. Any real permission prompt goes to the user. In the 2026-11-21 restricted install, `python -c` in a pipeline prompted ("script execution via -e/-c flag") and was denied after 5 minutes unanswered; notify the user at once. |
| Model | `hermes -p <profile> config set model.default <model>`; set `model.provider` when needed for the chosen integration. Ingest trials use GPT-6 Luna (`openai-codex`, `gpt-6-luna`) from 2026-11-21; Haiku is planned as a second ingest model. Installs use the latest Sol. |
| Invoke | `--skills obsidian-ingest` plus a bare URL: Hermes is the harness where a bare URL must work, because users send links from chat apps such as Telegram. Keep the `skills` toolset enabled so the text reaches the model; the activated-skill banner alone is not proof. |
| Transcripts | `hermes -p <profile> sessions export <file>`; preserve the export and session-to-source mapping in the report. |
| Wait | Hermes did not report idle to `hc`. `scripts/trials/watch` counts its turn-summary lines (`^─ [0-9][0-9.ms]* · `); read the output when one appears. Do not block the orchestrator with a foreground sleep. |
| Traps | Trusted MCP tools sit behind tool search. Several trusted regression sessions never loaded the skill. Three rejected MCP calls pause the server for 60 seconds across every session in the profile; repeated calls during a pause extend it. Lower concurrency or use trial-profile clones if pauses cascade. NIM (Nemotron) held two parallel sessions reliably in the 2026-11-21 loop. Nemotron sometimes JSON-stringified `tool_call` `calls`; check drafts for probe/test notes. |
| Between-run updates | Restart only the trial MCP servers and clear the trial profile's `cache/mcp_schema_cache.json` and `tool_discovery_cache.json` after code/schema changes. Schemas are cached by config, not code. Do not update during a run. |
| Undo/cleanup | A restricted profile cannot undo itself: use another terminal-capable profile. Installs can write to `~/.local/share/obsidian-ingest/` outside the profile. Inspect those artifacts separately. Export before any `hermes profile delete <profile> -y`; deletion is trial cleanup, not the skill undo test. |

## opencode

| Item | Tester guidance |
|---|---|
| Launch | `scripts/trials/sandbox opencode agent` |
| Auto-approve | Wrapper uses `--auto`, only inside bwrap. |
| Model | The tested TUI had no model flag. Set `"model"` in the throwaway `opencode.jsonc` before the phase and verify the session's effective model. |
| Invoke | Ingest with `@obsidian-ingest <url>` (`hc send` works); the `skill` tool can load it too. A bare URL is only an optional discovery check. |
| Transcripts | `opencode session export <id>` from the isolated environment. Preserve the session IDs and exports before teardown. |
| Wait | The shared service made panes look continually busy. Use plain `hc send`, not `--if idle`, after checking readiness; read back and inspect completion in the transcript. |
| Service isolation | The real opencode shares one background service per user on a fixed port, so `prepare` gives the trial its own (`TRIAL_OPENCODE_PORT`, default 49391). Run service commands only inside the trial environment. Check `opencode debug paths` before any probe. |
| Config changes | Restarting the trial service after a model or `OPENCODE_CONFIG` change was an observed workaround. Current [configuration docs](https://opencode.ai/docs/config) describe hot reload; verify effective state rather than routinely restarting. Restart only an unhealthy trial service. |
| Traps | `opencode mcp add` needs `--global` for user scope. `mcp list` was unreliable; check `/mcps` in a session. The TUI had no `--agent`: shift+tab selects the advanced restricted agent. MCP goes through `execute` by default. |
| Limits/cleanup | Free models shared an account-wide daily cap; changing the free model did not bypass it. A bare Docker image was refused metered free models. A probe run outside isolation can leave rows in the real database that no CLI command deletes: never probe outside isolation. Compare real database counts afterward. |

The current [CLI guide](https://opencode.ai/docs/cli) also documents `--standalone`
private servers. Use the kit's isolated launch rather than changing the user's real
service settings. Do not delete or edit a real database to conceal trial contamination.

## herdr / `hc`

`hc` coordinates real harness sessions; it is not a replacement ingestion runner.

- `hc send` wraps text in `<hc from=…>` tags. Claude Code slash commands sent this
  way do not execute. Codex `$obsidian-ingest` and opencode `@obsidian-ingest` mentions
  worked.
- `hc keys` cannot type `:`, `_`, or capitals: shift is dropped on characters
  (`shift+;` types `;`). For a Claude Code slash command with a URL, use its
  non-interactive `-p` launch from the Claude Code table instead. Do not count a
  wrapped message as a successful invocation. hc's global `notes = false` setting
  removes the tags too, but it affects every hc session on the machine.
- Text sent before the TUI is ready is lost. Use `hc send --if idle` where supported,
  then `hc read` to confirm receipt. opencode needs the plain-send exception above.
- One opencode paste opened “No results found” and swallowed the URL. Discard that
  session as a delivery failure and retry in a fresh one; record both.
- Dismiss install question forms with Esc, then answer all choices in one chat message.
  The orchestrator answers disposable-trial choices, but never auto-approves real
  permission prompts outside bwrap.
