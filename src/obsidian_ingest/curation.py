"""Application functions for verified vault connections and edit proposals."""

import re

from obsidian_ingest.drafts import DraftResult, DraftWriterSettings, create_draft
from obsidian_ingest.extractor.metadata import SourceMetadata
from obsidian_ingest.vault import NoteChanged, NoteUnavailable, VaultReader

MAX_RELATED_NOTES = 5
MAX_REASON_LENGTH = 300


def verified_related_notes(
    vault: VaultReader, references: list[dict[str, str]] | None
) -> tuple[tuple[str, str], ...]:
    if references is None:
        return ()
    if not isinstance(references, list) or len(references) > MAX_RELATED_NOTES:
        raise ValueError(
            f"INVALID_ARGUMENT: related_notes must contain at most {MAX_RELATED_NOTES} references"
        )
    verified = []
    seen = set()
    for item in references:
        if (
            not isinstance(item, dict)
            or not {"note_id", "reason"} <= set(item)
            or set(item) - {"note_id", "reason", "revision"}
        ):
            raise ValueError("INVALID_ARGUMENT: each related note needs note_id and reason")
        note_id, reason = item["note_id"], item["reason"]
        if not all(isinstance(value, str) for value in (note_id, reason)) or (
            "revision" in item and not isinstance(item["revision"], str)
        ):
            raise ValueError("INVALID_ARGUMENT: related note fields must be strings")
        if (
            not reason.strip()
            or len(reason) > MAX_REASON_LENGTH
            or "\n" in reason
            or "\r" in reason
        ):
            raise ValueError("INVALID_ARGUMENT: related note reason must be a short single line")
        if "[[" in reason or "]]" in reason:
            raise ValueError("INVALID_ARGUMENT: related note reason must not contain wikilinks")
        try:
            reference = vault.get_linkable_reference(note_id)
        except NoteUnavailable as error:
            raise ValueError("NOTE_UNAVAILABLE: related note is unavailable") from error
        if not reference.linkable:
            raise ValueError("NOTE_NOT_LINKABLE: related note has a path that cannot be wikilinked")
        if reference.path in seen:
            continue
        seen.add(reference.path)
        verified.append((f"[[{reference.path}]]", reason.strip()))
    return tuple(verified)


def create_edit_suggestion(
    settings: DraftWriterSettings,
    *,
    source: SourceMetadata,
    vault: VaultReader,
    target_note_id: str,
    target_revision: str,
    change_summary: str,
    reasoning: str,
    proposed_content: str,
    timestamps: tuple[float, ...] = (),
) -> DraftResult:
    """Write a human-review-only proposal linked to an unchanged existing note."""
    if (
        not isinstance(change_summary, str)
        or not change_summary.strip()
        or len(change_summary) > 500
    ):
        raise ValueError(
            "INVALID_ARGUMENT: change_summary must be nonempty and at most 500 characters"
        )
    if "\n" in change_summary or "\r" in change_summary:
        raise ValueError("INVALID_ARGUMENT: change_summary must be one line")
    if not isinstance(reasoning, str) or not reasoning.strip() or len(reasoning) > 3000:
        raise ValueError("INVALID_ARGUMENT: reasoning must be nonempty and at most 3000 characters")
    if "\n" in reasoning or "\r" in reasoning:
        raise ValueError("INVALID_ARGUMENT: reasoning must be a single paragraph")
    if (
        not isinstance(proposed_content, str)
        or not proposed_content.strip()
        or len(proposed_content) > 20_000
    ):
        raise ValueError(
            "INVALID_ARGUMENT: proposed_content must be nonempty and at most 20000 characters"
        )
    try:
        target = vault.get_reference(target_note_id, target_revision)
    except NoteUnavailable as error:
        raise ValueError("NOTE_UNAVAILABLE: target note is unavailable") from error
    except NoteChanged as error:
        raise ValueError("NOTE_CHANGED: target note changed; search and read it again") from error
    if not target.linkable:
        raise ValueError("NOTE_NOT_LINKABLE: target note has a path that cannot be wikilinked")

    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", target.title).strip(" .-") or "Note"
    prefix = "(EDIT) "
    title_bytes = title.encode("utf-8")[: 240 - len(prefix.encode())]
    while True:
        try:
            title = title_bytes.decode("utf-8").rstrip(" .-") or "Note"
            break
        except UnicodeDecodeError:
            title_bytes = title_bytes[:-1]
    contents = (
        f"## Suggested Change\n\n{change_summary.strip()}\n\n"
        f"## Why\n\n{reasoning.strip()}\n\n"
        f"## Proposed Content\n\n{proposed_content.strip()}"
    )
    return create_draft(
        settings,
        title=prefix + title,
        contents=contents,
        source=source,
        timestamps=timestamps,
        leading_link=f"[[{target.path}]]",
    )
