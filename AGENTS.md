# Project Purpose

This private project is a personal Obsidian knowledge-ingestion tool and a Python
learning project. Extract durable knowledge from URLs through a deterministic
CLI/pipeline. It is not a generic YouTube summarizer, an autonomous agent, or a
general-purpose ingestion framework. Prefer understandable code over cleverness.

# Engineering Approach

- Use Python, uv, pyproject.toml, a src/ layout, Ruff, pytest, and type hints.
- Prefer functions, small pure transformations, frozen dataclasses for internal
  values, and enums for meaningful domain concepts. Use tuples for immutable collections.
- Extend existing modules when sensible; do not create one file per class.
- Add dependencies only for concrete needs. Do not introduce LangChain, agent
  frameworks, dependency injection, abstract provider hierarchies, repositories,
  service/controller layers, plugin registries, premature async, or speculative
  strategy/factory patterns. Pydantic requires an actual validation need.
- Do not add compatibility layers without shipped consumers or persisted data
  that require them. Keep refactors focused and preserve unrelated work.

# Architecture and Boundaries

The conceptual flow is:

```text
URL -> detect provider -> provider-specific extraction -> normalized metadata
    -> transcript/captions -> application content category
    -> future segmentation -> useful knowledge classification
    -> similarity/deduplication against Obsidian -> draft notes
```

Provider-specific modules are intentional. YouTube is the first implemented
provider; Instagram Reels and TikTok are future sources. Recognizing a hostname
does not mean extraction is implemented. Parse hostnames, never detect providers
through arbitrary URL substring matching. Simple detection and dispatch suffice.

yt-dlp is the current extraction dependency for metadata and caption discovery.
Provider modules translate external dictionaries into internal domain models;
downstream processing must not depend on yt-dlp field names. Do not mirror all
external fields or retain irrelevant download/engagement metadata.

Use composition: SourceMetadata owns common fields and optional provider_details,
such as YouTubeMetadata. Do not subclass SourceMetadata for each platform or
invent provider detail schemas before implementing those providers. Chapters are
provider-agnostic timestamped metadata, not YouTube-only objects.

Keep these concepts separate:

- SourceMetadata describes the source, creator, provenance, and available context.
- Native format describes presentation, such as standard video versus a native
  vertical short. Duration alone does not establish native format.
- Caption tracks and selection describe how textual material is retrieved; they
  do not belong in SourceMetadata.
- Transcript contains normalized textual source material with timestamps.
- TranscriptSegment is a source/timestamp atom, not a semantic chunk or sentence.
- Content category is a deterministic, provider-agnostic application decision,
  not an external provider field or an AI classification.

Current code organization: extractor/__init__.py owns shared provider detection
and enums; extractor/metadata.py owns metadata models, categorization, and metadata
dispatch; extractor/transcript.py owns transcript models, dispatch, and printing;
extractor/providers/ owns provider implementation. Keep package initializers free
of eager provider loading; function-local provider imports avoid cycles between
dispatch and the models consumed by providers.

# Provenance and Safety

Preserve source URLs, source timestamps, and original transcript wording. Current
JSON3 events remain atomic; do not merge them into sentences or semantic chunks
as part of extraction. Timing values use seconds.

Any future transcript cleanup must preserve raw material separately. Technical
ASR repair may use title, description, chapters, and optional retrieved context,
but must be conservative. Never silently alter factual claims, numbers,
measurements, or versions. Missing information should stay unknown rather than
be guessed; document lossy conversions and inference limits.

Future notes go to an AI Drafts/Inbox area. The program must not freely modify
canonical Obsidian notes. Preserve provenance in generated drafts.

# Roadmap, Not Current Functionality

The implemented foundation is YouTube caption extraction, timestamped transcript
output, source metadata normalization, and deterministic content categorization.
The CLI currently prints transcripts; metadata is also available through Python
functions. Do not assume the following stages already exist:

1. Extend provider-specific extraction to other sources when needed.
2. Develop category-informed segmentation using real examples. Clips may need no
   chunking; short-form material may work as a whole; long-form material should
   prefer chapters; extended material may require hierarchical processing.
3. Classify useful, durable knowledge rather than merely summarize everything.
4. Retrieve similar existing notes with embeddings and deduplicate conservatively.
5. Generate provenance-preserving draft notes for review.

Possible future model roles include Jev or another decision/classification model
and Nemotron or another note-writing model. These are candidates, not dependencies
or fixed architectural choices. Keep models, API vendors, embedding providers,
and optional technical-context retrieval replaceable. Do not implement later AI,
chunking, normalization, or Obsidian stages unless explicitly requested.

# Working and Verification

Inspect current code and worktree state before editing. Explain a concise plan
for substantial changes, implement the smallest coherent solution, then verify.
This is a learning project: keep decisions and limitations understandable.

Use small fake extraction dictionaries and mocked I/O in tests. The test suite
must not call YouTube or other live services. Live CLI checks are optional and
separate from automated tests. Preserve transcript output and source granularity.

```bash
uv sync
uv run ruff format src tests
uv run ruff check src tests
uv run pytest
git diff --check
```

Review the resulting diff for unrelated edits and unnecessary abstraction. Report
test results and limitations honestly. Do not commit or push unless requested.
Keep this guide about durable project context and architecture, not session logs,
temporary failures, feature checklists, or fixture-specific details.
