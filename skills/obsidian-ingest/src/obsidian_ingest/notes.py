"""A vault note handed to a provider as a saved source (such as a Web Clipper page)."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ClippedNote:
    """A note's vault-relative path, revision and full text, read through the vault policy."""

    path: str
    revision: str
    text: str
