"""In-body exclusions for text sources, the counterpart of video sponsor segments.

Page chrome is pruned before extraction; these rules mark blocks inside the article
body that are not its content. Excluded atoms stay in the source, readable with
``include_excluded``, and each exclusion records the rule that matched it.

- ``sponsored``: sponsorship, paid-partnership and affiliate disclosures.
- ``promo``: short subscribe, donate, sign-up and "read more" calls to action, and
  bare "Advertisement" labels.
- ``duplicate``: a block repeating an earlier one (pull quotes, repeated captions).
"""

import re

from obsidian_ingest.extractor.metadata import ExcludedRange

CATEGORIES = ("sponsored", "promo", "duplicate")
PROMO_MAX_WORDS = 40
_SPONSORED = re.compile(
    r"^\W*(this (post|article|story|piece|episode|newsletter) (is|was) "
    r"(sponsored|brought to you|presented)|sponsored (by|content|post)|paid (post|partnership|content)|"
    r"(this (post|article|page) )?contains affiliate links|we (may )?earn (a )?commission|"
    r"affiliate disclosure|in partnership with)",
    re.IGNORECASE,
)
_PROMO = re.compile(
    r"^\W*(advertisement|sponsored|subscribe( now| today| for)?|sign up( for| to| now)|"
    r"join (our|the) (newsletter|mailing list|community)|get (our|the) newsletter|"
    r"support (us|our (work|journalism))|donate( now| today)?|become a (member|patron|subscriber)|"
    r"read (more|next|also)\s*:|related\s*:|recommended( for you)?\s*:|follow us)",
    re.IGNORECASE,
)


def _normalized(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def find_exclusions(
    texts: list[str], kinds: list[str], categories: tuple[str, ...] = CATEGORIES
) -> tuple[ExcludedRange, ...]:
    """Return one [index, index + 1) range per excluded atom, in source order."""
    ranges: list[ExcludedRange] = []
    seen: set[str] = set()
    for index, (text, kind) in enumerate(zip(texts, kinds, strict=True)):
        words = len(text.split())
        reason = rule = None
        if "sponsored" in categories and kind != "code" and _SPONSORED.search(text[:200]):
            reason, rule = "sponsored", "heuristic:disclosure"
        elif (
            "promo" in categories
            and kind not in {"code", "table_row"}
            and words <= PROMO_MAX_WORDS
            and _PROMO.search(text)
        ):
            reason, rule = "promo", "heuristic:call-to-action"
        key = _normalized(text)
        if (
            reason is None
            and "duplicate" in categories
            and kind != "code"
            and key in seen
            and (words >= 4 or kind in {"caption", "quote"})
        ):
            reason, rule = "duplicate", "heuristic:repeated-block"
        if key:
            seen.add(key)
        if reason is not None:
            ranges.append(ExcludedRange(index, index + 1, reason, rule))
    return tuple(ranges)
