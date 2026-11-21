# Trial report template

Copy this template to
`trial-reports/<date>-<harness>-<name>/REPORT.md`. `trial-reports/` is gitignored;
this public template stays in `docs/`. Follow [the protocol](live-trials.md).
Keep transcripts and `audit.md` beside the report. Do not include credentials or
private files. Replace placeholders and remove instructional text.

---

# <Harness> live trial — <date>, <name>

## Outcome

<State what passed, failed, or remains unverified. Separate install end state,
workflow compliance, and draft usefulness. Zero notes is not automatically a failure.>

## Setup

- Harness and version:
- Install / repeat-install / undo model and provider:
- Ingest model and provider:
- Thumbnail model and image capability:
- Skill commit from `git HEAD`:
- Trial method: <bwrap v2 / Hermes throwaway profile>
- Profile or trial identifier:
- Setup mode and effective tools:
- Sandbox selftest result and evidence:
- Source selection: <manifest subset and why>
- Parallelism, provider limits, and retries:
- Known deviations or orchestrator assistance:
- Earlier reports read:

Do not expose credentials or unnecessary machine-specific paths. Record tested
versions as observations, not requirements. Note that sandbox auto-approve hides the
permission prompts a normal user might see.

## Phases

Use `pass`, `partial`, `fail`, `blocked`, or `not tested`, with concrete evidence.
A successful final state does not erase a missing approval or an interim overwrite.

| Phase | Verdict | Session / evidence | Findings or remaining work |
|---|---|---|---|
| Fresh install | | | |
| Repeat install | | | |
| Ingest (bare URL on Hermes, invocation elsewhere) | | | |
| Explicit invocation | | | |
| Restricted setup / advanced launch | | | |
| Thumbnail delivery | | | |
| Undo | | | |
| Real config and canonical-note comparison | | | |

### Install and undo details

<Record scope choices, plan/approval turns, backups, skill/settings/cache locations,
registration, effective tool listing, skill-text probe, repeated configuration,
later-config-edit handling, and what undo left behind. Name unverified checks.>

## Sources

Give one row per source/session, including blocked, retried, and zero-note cases.
Link timestamps, transcript evidence, and draft files where useful.

| URL / source | Session / model | Phase / invocation | Verdict | Reads, searches, errors | Created paths or zero-note reason | Usefulness and defects |
|---|---|---|---|---|---|---|
| <exact URL> | | <bare URL / explicit / restricted> | | | | |

## Draft review

<Inspect the actual disposable vault, not just final messages. Discuss atomicity,
own words, useful links, source fidelity, attribution, timestamps, edit-suggestion
scope, overlap, and whether the notes are worth rereading months later. Explain weak
or redundant drafts and justified zero-note outcomes. Counts of useful notes are fine;
do not add numeric scores, percentages as quality targets, or golden prose.>

## Evidence and safety checks

- Transcript files and session-to-phase/source mapping:
- Auditor model, [audit](audit.md), and any evidence gaps:
- Actual vault and draft evidence reviewed:
- Before/after snapshots and comparison result:
- Canonical notes and excluded-path canary:
- User's real harness config/data, including database counts where applicable:
- Interim writes or leaked tools seen in transcripts:
- Trial daemons stopped:
- Report archived before teardown:
- Cleanup completed and any trial-owned leftovers:

## Agent mistakes

List concrete mistakes, including recovered errors and orchestrator mistakes.
Separate product/skill failures, harness behavior, provider failures, and prompt
delivery failures. Include the evidence and consequence; do not hide retries.

- <Session, action, violated instruction, consequence, recovery if any.>

## Fixes after the run

Keep the run's verdict about the tested revision. Do not imply fixes have been tested.

- <Change, reason, and the fresh phase/run needed to verify it.>

## Limitations and next verification

<Missing phases, model/provider outages, permission prompts hidden by auto-approve,
missing transcripts, cleanup gaps, and any other reason not to generalize the result.>
