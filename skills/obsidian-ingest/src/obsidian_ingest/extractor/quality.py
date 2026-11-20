"""Extraction quality for text sources: fail only on clear evidence; short is not bad.

A candidate fails when it has almost no text, is mostly links, is mostly duplicated,
or shows a paywall together with a body far shorter than the page declares. Weaker
signals only add warnings ("degraded"), and the source still succeeds. Visible text
that addresses the agent is flagged, not removed: an article may be about it.
"""

import re
from dataclasses import dataclass, field

MIN_WORDS = 50
SHORT_WORDS = 150
MAX_LINK_DENSITY = 0.6
MAX_DUPLICATE_RATIO = 0.5
TRUNCATION_RATIO = 0.5
_INSTRUCTION = re.compile(
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?)|"
    r"disregard (all |any )?(previous|prior|your) instructions|system prompt|"
    r"you are (an?|the) (ai|assistant|language model|llm)|"
    r"\b(create_draft|create_edit_suggestion|read_source|search_vault|ingest_source)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Quality:
    """``status`` is ok, degraded or failed; ``reason`` is set only when failed."""

    status: str
    reason: str | None
    words: int
    link_density: float
    duplicate_ratio: float
    warnings: tuple[str, ...] = field(default=())

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def score(self) -> tuple[int, int]:
        """Order candidates: any success beats failure, then more words win."""
        return (0 if self.failed else 2 if self.status == "ok" else 1, self.words)


def assess(
    words: int,
    link_words: int,
    duplicate_atoms: int,
    atoms: int,
    *,
    declared_words: int | None = None,
    paywall_signals: tuple[str, ...] = (),
    replacement_count: int = 0,
) -> Quality:
    """Judge one extraction candidate from counts the extractor already has."""
    density = round(link_words / words, 3) if words else 0.0
    duplicates = round(duplicate_atoms / atoms, 3) if atoms else 0.0
    warnings: list[str] = []
    truncated = bool(declared_words) and words < TRUNCATION_RATIO * declared_words
    reason = None
    if paywall_signals and (truncated or words < SHORT_WORDS):
        reason = "PAYWALL_SUSPECTED"
    elif words < MIN_WORDS or density > MAX_LINK_DENSITY:
        reason = "NO_ARTICLE_TEXT"
    elif duplicates > MAX_DUPLICATE_RATIO:
        reason = "LOW_QUALITY"
    if words < SHORT_WORDS:
        warnings.append("short_text")
    if truncated:
        warnings.append("possibly_truncated")
    if paywall_signals:
        warnings.append("paywall_signals:" + ",".join(paywall_signals))
    if replacement_count > 10:
        warnings.append("encoding_replacements")
    status = "failed" if reason else "degraded" if warnings else "ok"
    return Quality(status, reason, words, density, duplicates, tuple(warnings))


def instruction_like(texts: list[str]) -> list[int]:
    """Indices of atoms whose visible text addresses an AI agent or names its tools."""
    return [index for index, text in enumerate(texts) if _INSTRUCTION.search(text)]
