# Live trials

Live trials check install UX, effective permissions, source navigation, and the
usefulness of agent-written drafts in a disposable vault. They are not CI, a benchmark,
or a scoring framework. Agents judge the results; scripts handle mechanical setup and
safety checks. Writing zero notes can be the right outcome.

Read earlier reports and their agent-mistake lists before starting. Use the current
[install steps](../skills/obsidian-ingest/references/install.md) and
[skill workflow](../skills/obsidian-ingest/SKILL.md) as the contract, not an older
report's instructions. See the [harness tables](live-trials-harnesses.md) for observed
quirks. Confirm commands with the installed harness's help; tested versions are
observations, not requirements.

## Boundaries

Use only the public fixture vault in `tests/fixtures/trial-vault/`, never a real vault.
Select sources from `videos.txt` and record the exact URLs. Include a long tutorial,
existing-note overlap, tips, and a source that should produce no notes when practical.
Never copy private vaults, media, or credentials into the repository.

Hermes uses an `hc` child in a fresh throwaway profile. Abort if a throwaway profile
cannot be created. This is profile isolation, not an OS sandbox: installs can write
outside the profile. Snapshot those real locations and inspect them afterward.
Do not use auto-approve outside an OS sandbox; the user handles permission prompts.

Claude Code, Codex, and opencode use the Linux-only bubblewrap (`bwrap`) method:

- The system is read-only. The real home is hidden by a throwaway home bind-mounted
  **at the real `$HOME` path**. Literal home paths and `$HOME` therefore agree.
- Only the harness and `uv` binary directories needed under that home are exposed
  read-only. Resolve real binaries, not mise shims that depend on the original home.
- The trial home is writable. The inherited environment is cleared, so no API keys
  or agent sockets leak in; XDG folders fall back to their defaults under the
  throwaway home, and `TMPDIR` and `XDG_RUNTIME_DIR` point into it. `/tmp`,
  `/run/user`, and removable-media mount points are private. Pass a variable through
  on purpose with `TRIAL_PASS_ENV="NAME1 NAME2"`.
- The rest of the system (`/etc`, `/usr`, other users' world-readable files) stays
  readable. The sandbox hides the tester's home; it is not a confidentiality boundary
  for the whole machine.
- Network stays on. PID and IPC namespaces are separate, and children die with the
  sandbox parent.
- Notes and snapshots remain outside the child-visible home. Do not leak their paths
  through environment variables, launchers, or prompts.

Auto-approve is allowed only inside this verified OS sandbox. It removes approval
friction, but does **not** measure which permission prompts an ordinary user would see.
Trusted/restricted skill settings and the outer trial sandbox are different boundaries.
MCP alone is not an OS sandbox.

## Prepare and verify

Run commands from the repository root. The first argument is `claude`, `codex`,
`opencode`, or `hermes`. See each script's `--help` for details.

```bash
scripts/trials/prepare claude
scripts/trials/selftest claude
scripts/trials/snapshot claude
scripts/trials/prepare claude --refresh-credentials   # right before each later run
scripts/trials/sandbox claude agent --model <model>
```

Do not launch any agent unless `selftest` passes. It must show that real home files
are invisible, writes outside the allowed trial paths fail, and the harness, `uv`,
and network work. Stop on failure; do not weaken isolation to continue.

`TRIAL_ROOT` overrides the default base
`${XDG_CACHE_HOME:-$HOME/.cache}/obsidian-ingest-trials`. Each harness gets:

```text
<trial root>/<harness>/
  home/
    trial/skill/obsidian-ingest/
    trial/vault/
    trial/work/
  notes/
```

The skill is copied from `git HEAD`, not uncommitted work. Inside the sandbox its
entry point is `~/trial/skill/obsidian-ingest/SKILL.md`. The vault is copied from the
fixture, and sessions start in the scratch work directory, outside the repository.
`notes/` is outside the child-visible home; with a `TRIAL_ROOT` outside `$HOME` the
sandbox covers the whole trial root, and `selftest` checks that the notes are invisible.
`TRIAL_EXTRA_RO_BINDS` exposes extra paths read-only; `selftest` warns about them and
the tester answers for what they reveal.

`prepare` refuses an existing trial root. It copies only the selected harness's
credentials, never prints them, and pre-seeds onboarding and folder trust. For
opencode, select an integration with `prepare opencode --integration NAME` and a
model with `--model MODEL`.
Refresh credentials immediately before each run with
`prepare <harness> --refresh-credentials`; copied Claude logins can lapse within hours.
Never send a task to an unauthenticated or unexpectedly billed probe.

For Hermes, `prepare hermes` creates a throwaway profile instead of copying
credentials. `TRIAL_HERMES_PROFILE` selects its name (default `oikit`); the name is
recorded in the trial root, and `teardown hermes` deletes only that recorded profile. Use the profile
launch in the harness table, not `sandbox hermes`. Run the applicable snapshot and
selftest checks before launching it.

## Run phases

Install with a frontier model. Ingest with a weaker or open-weight model so the skill,
not the installer's reasoning ability, carries the workflow. Record the exact models
and any substitutions; no model family is required.

Send only an install sentence pointing at the trial copy's `SKILL.md`. Do not preload
the install answers or coach the child through the instructions. The orchestrator
answers choice and approval messages on behalf of the disposable trial. It must not
approve changes outside the agreed trial scope.

If a harness opens a question form, dismiss it with Esc and answer all choices in one
chat message. Wait for the installer's separate plan and approval turn. Auto-approve
must not excuse a missing plan. Outside bwrap, real permission prompts go to the user.

| Phase | Pass criteria |
|---|---|
| Fresh install | Identifies the harness, inspects existing state, asks scope choices once, shows a plan and waits for approval before install changes. Backs up edited host config; preserves unrelated settings. Uses a dedicated skill copy and cache outside the vault. Verifies connectivity, settings, skill text, and effective tools in a fresh session. Reports paths, checks, limitations, start command, and undo. No notes are created during setup. |
| Repeat install | Reuses valid settings, skill link, MCP registration, and cache. Asks only about missing or wrong choices. When nothing will change, shows the settings and runs the checks without an approval turn; any change, including an update, needs the plan and approval. Does not duplicate configuration or broaden scope. Preserves unrelated config. No skill is created or edited during install, repeat install, or undo; on Hermes restricted mode `curator status` shows the skill locked. |
| Ingest | Each fresh session receives the URL the way users are told to send it: on Hermes the bare URL only, elsewhere the harness's skill invocation with the URL. It loads the skill. Reads every transcript page in order, searches and reads relevant vault notes, then creates useful drafts or edit suggestions, or explains a justified zero-note result. Reports only successful writes. Canonical notes and exclusions remain untouched. |
| Explicit invocation | Outside Hermes this is the ingest phase above: use the harness's actual skill invocation, not a wrapped chat message that merely resembles a slash command. A bare URL outside Hermes is an optional discovery check, reported but not a failure. |
| Restricted setup | Hermes offers a distinct restricted profile. Other harnesses install trusted and test only the documented advanced restricted launch. Inspect actual tools in a fresh session: keep the skill loader and intended MCP tools; identify any leaked tools. Never claim a complete restriction when undeletable tools remain. Verify configured exclusions and canary privacy without granting access to real files. |
| Thumbnail | With an image-capable model, explicitly request `get_thumbnail` for a source and verify the model receives and can describe the image. A successful tool response alone is not proof of image delivery. If unavailable, mark the phase not tested or blocked; thumbnails remain optional for normal ingestion. |
| Undo | From a session with terminal access, asks before changes and checks for later config edits. Removes the registration and skill link, restores only applicable backed-up settings, and verifies the result. Leaves runtime, settings, cache, vault, and published notes unless explicitly asked otherwise. |

For restricted launches, compare the advanced launch's own deny/approval policy with
the trial wrapper's auto-approve policy. Do not let the wrapper silently undo the
restriction being tested. Record both the effective tool list and any residual
approval-dependent protection.

Use separate sessions for verification probes and ingestion. Do not mistake a banner
for proof that `SKILL.md` reached the model. Follow each harness's documented skill-load
probe. Inspect the actual vault after each phase; transcript claims are not enough.
Check restart/cache reuse where relevant and distinguish reuse from a fresh capture.

## Parallelism and waiting

Run ingestions in parallel fresh sessions, normally in batches of five. About three
sessions per batch is a better starting point for NVIDIA NIM's advertised “up to
40 rpm”; two at a time ran without queuing in the 2026-11-21 Hermes loop. Reduce parallelism on HTTP 429 or provider queuing and record the change.
A rate-limited run measures the provider, not the skill. Harnesses using independent
subscriptions can run in parallel.

Wait with a background watcher, not a foreground sleep, a timeout or a blocking `hc wait`
on one child. `scripts/trials/watch` prints one line each time a child of the calling
`hc` terminal stops: run it under the orchestrator's background monitor (for example a
Claude Code `Monitor`) and answer each child as soon as its line arrives, while the
others keep working. It debounces Codex's brief mid-turn idles and counts Hermes turn
summaries, since Hermes does not report status. A restarted watcher records children that
are already stopped without printing them, so read those yourself. Confirm the TUI is
ready before sending a prompt, and read it back afterward.

Usage checks are not part of the protocol (tester decision, 2026-11-21). If a tester
asks for one, `scripts/trials/usage claude` or `scripts/trials/usage codex` reads
`/usage` or `/status` in a throwaway `hc` child; run it only between complete runs.

Keep code, skill text, and installed schemas fixed throughout a run. Finish and report,
then fix and start a new run. Refreshing credentials is not a product fix. After a
Hermes code/schema update between runs, restart its trial MCP servers and clear the
trial profile's schema/discovery caches as described in the harness table.

## Evidence, comparison, and cleanup

Write `trial-reports/<date>-<harness>-<name>/REPORT.md` using the
[report template](trial-report-template.md). Reports are gitignored. Preserve session
transcripts, draft evidence, and the auditor's `audit.md` there, without credentials.
Keep tester notes and snapshots invisible to children.

Have an auditor read the transcripts itself using the
[audit prompt](trial-audit-prompt.md). Review install steps, every ingestion workflow,
actual Markdown, and concrete mistakes. Judge a human-first Zettelkasten: atomic
ideas, own words, useful links, source fidelity, and something worth rereading.
There is no target note count, golden prose, numeric quality score, or claim that
source attribution proves a claim true.

After agents and trial daemons are stopped, compare before cleanup:

```bash
scripts/trials/compare claude
```

`snapshot` writes `notes/snapshot-before.txt`; `compare` repeats the fingerprints and
exits non-zero on a difference. Checks cover the user's real harness configuration
and skills folders, the real install locations of this skill, opencode database
counts, and every vault file outside the drafts folder. Only hashes and counts are
stored. Using the same harness yourself during a run can change a fingerprint. Review
differences; do not restore unrelated user work just to make the comparison pass.
An unchanged final snapshot does not excuse a transcript showing an interim overwrite.

Archive reports before teardown:

```bash
scripts/trials/teardown claude
```

Teardown is destructive cleanup, not the skill's undo test. It stops the trial's
sandboxes (their daemons end with them) before removing a fixed list of paths confined
to the trial root; `--keep-notes` keeps `notes/`. For Hermes, inspect the install
report for artifacts outside its profile and the trial root. Remove only explicitly
approved trial-owned artifacts; never assume deleting the profile removes them.
Report any leftovers or unverified cleanup. Do not change the user's real harness
database or configuration to hide trial contamination.
