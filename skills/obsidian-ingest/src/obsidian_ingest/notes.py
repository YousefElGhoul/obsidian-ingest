"""A vault note handed to a provider as a saved source (such as a Web Clipper page)."""

import hashlib
from dataclasses import dataclass
from pathlib import Path


class InvalidNoteSource(ValueError):
    """A saved note whose source cannot be ingested as that kind of note."""


def vault_identity(root: Path) -> str:
    """A short, stable identity for one vault, without exposing its path."""
    return hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ClippedNote:
    """A note's vault-relative path, revision and full text, read through the vault policy.

    ``vault_id`` identifies the vault it was read from, so a capture made from it is only
    served to sessions that can open the same note in the same vault.
    """

    path: str
    revision: str
    text: str
    vault_id: str = ""


@dataclass(frozen=True, slots=True)
class NoteRef:
    """The note a capture depends on: which vault, and which vault-relative path."""

    vault_id: str
    path: str
