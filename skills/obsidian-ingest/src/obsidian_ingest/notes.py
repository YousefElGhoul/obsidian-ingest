"""A vault note handed to a provider as a saved source (such as a Web Clipper page)."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


class InvalidNoteSource(ValueError):
    """A saved note whose source cannot be ingested as that kind of note."""


def vault_identity(root: Path, *, root_fd: int | None = None) -> str:
    """Identify the resolved root and its directory without exposing the path."""
    resolved = root.resolve() if root_fd is None else root
    stat = os.stat(resolved if root_fd is None else root_fd)
    identity = f"{resolved}\0{stat.st_dev}\0{stat.st_ino}"
    return hashlib.sha256(os.fsencode(identity)).hexdigest()[:16]


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
