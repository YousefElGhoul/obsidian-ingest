# Contributing

Obsidian Ingest is an alpha. The design is still moving, so small, focused
contributions are the most useful. Today it covers Linux, YouTube videos with English
captions, and the harnesses listed under
[tested harnesses](README.md#tested-harnesses).

## What helps most

- **Bug reports.** Include the harness and its version, the model, the source URL, the
  command or tool call, and the error code or output. Leave out vault contents,
  credentials, and session exports.
- **Live-trial results** for a harness or version, following the
  [trial protocol](docs/live-trials.md) and the
  [report template](docs/trial-report-template.md). Reports stay on your machine; share
  the findings in an issue.
- **Small fixes**: documentation, harness notes in
  [`skills/obsidian-ingest/references/`](skills/obsidian-ingest/references/), and tests.

## Before you write code

Open an issue first for anything beyond a small fix, such as a new source provider, a
new harness, or a new tool or setting. It saves you from building something that will
be declined. These are out of scope:

- Editing, moving, or deleting existing notes, or any unsafe writer mode.
- A bundled agent runner, or a scoring or benchmark framework.
- Classifiers, embeddings, or semantic segmentation without evidence they are needed.
- Cache migrations or stored evidence history; the SQLite cache is disposable.

## Development setup

You need Linux, [uv](https://docs.astral.sh/uv/), and Python 3.13+.

```bash
uv sync --project skills/obsidian-ingest --locked
```

Run the checks before opening a pull request. CI runs the same ones.

```bash
uv run --project skills/obsidian-ingest ruff format skills/obsidian-ingest/src tests scripts
uv run --project skills/obsidian-ingest ruff check skills/obsidian-ingest/src tests scripts
uv run --project skills/obsidian-ingest pytest
git diff --check
```

If you change `scripts/trials/`, also run:

```bash
shellcheck -S warning scripts/trials/prepare scripts/trials/sandbox scripts/trials/selftest \
  scripts/trials/snapshot scripts/trials/teardown scripts/trials/usage
shellcheck -S warning -e SC2034 scripts/trials/lib.sh
```

See [docs/development.md](docs/development.md) for more, and
[docs/reference.md](docs/reference.md) for the CLI, MCP, and error contracts.

## Rules for changes

- The CLI and MCP stay one-to-one: the same operations, validation, limits, results,
  and errors. Change both or neither.
- Automated tests use temporary directories and fake extraction data. They never touch
  a real vault or a live service.
- The skill directory `skills/obsidian-ingest/` stays self-contained, so it can be
  distributed as it is.
- Add a dependency only for a concrete need.
- Changes to the install steps or the skill text affect agent behaviour. Run a live
  trial, or say in the pull request that none was run.

[AGENTS.md](AGENTS.md) holds the full engineering boundaries.

## Pull requests

- One focused change per pull request, with the checks passing.
- Use [Conventional Commits](https://www.conventionalcommits.org/):
  `type(scope): summary`, then a short bulleted body describing what changed.
- Say what you verified and what you did not.
- Using a coding agent is fine. Have it follow [AGENTS.md](AGENTS.md), and review its
  work yourself before you submit it.

## Security and privacy

If you find a way around the vault scope or the create-only writer, do not open a
public issue. Email contact@yousefelghoul.dev with the steps to reproduce it. Never
attach private notes, tokens, or session exports to an issue or pull request.

## License

By contributing, you agree that your contributions are licensed under the
[Apache License 2.0](LICENSE), the same license as the project.
