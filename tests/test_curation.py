from pathlib import Path

import pytest

from obsidian_ingest.config import VaultSettings
from obsidian_ingest.curation import create_edit_suggestion, verified_related_notes
from obsidian_ingest.drafts import DraftWriterSettings
from obsidian_ingest.vault import VaultReader


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "Notes").mkdir(parents=True)
    (root / "Notes/Retry.md").write_text("Retry budgets limit load.", encoding="utf-8")
    (root / "Notes/Odd#Name.md").write_text("Retry with an unlinkable path.", encoding="utf-8")
    return root


@pytest.fixture
def reader(vault_root: Path):
    with VaultReader(VaultSettings(vault_root, ("*",), ())) as vault:
        yield vault


@pytest.fixture
def notes(reader: VaultReader) -> dict[str, dict]:
    return {note["path"]: note for note in reader.search("retry")["notes"]}


def _drafts(root: Path) -> list[Path]:
    folder = root / "00 Inbox/AI Drafts"
    return sorted(folder.iterdir()) if folder.exists() else []


def test_no_related_notes_is_an_empty_tuple(reader) -> None:
    assert verified_related_notes(reader, None) == ()
    assert verified_related_notes(reader, []) == ()


def test_related_reason_is_stripped_and_revision_is_optional(reader, notes) -> None:
    note = notes["Notes/Retry.md"]
    verified = verified_related_notes(
        reader,
        [
            {"note_id": note["note_id"], "reason": "  Shared idea.  "},
            {"note_id": note["note_id"], "reason": "Duplicate.", "revision": note["revision"]},
        ],
    )
    assert verified == (("[[Notes/Retry.md]]", "Shared idea."),)


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ("Notes/Retry.md", "needs note_id and reason"),
        ({"note_id": "x"}, "needs note_id and reason"),
        ({"note_id": "x", "reason": "r", "extra": 1}, "needs note_id and reason"),
        ({"note_id": 5, "reason": "r"}, "fields must be strings"),
        ({"note_id": "x", "reason": 5}, "fields must be strings"),
        ({"note_id": "x", "reason": "r", "revision": 5}, "fields must be strings"),
        ({"note_id": "x", "reason": "   "}, "short single line"),
        ({"note_id": "x", "reason": "a" * 301}, "short single line"),
        ({"note_id": "x", "reason": "two\nlines"}, "short single line"),
        ({"note_id": "x", "reason": "two\rlines"}, "short single line"),
        ({"note_id": "x", "reason": "see [[Other]]"}, "must not contain wikilinks"),
        ({"note_id": "x", "reason": "half]]"}, "must not contain wikilinks"),
    ],
)
def test_malformed_related_note_is_an_invalid_argument(reader, item, message) -> None:
    with pytest.raises(ValueError, match=f"INVALID_ARGUMENT: .*{message}"):
        verified_related_notes(reader, [item])


@pytest.mark.parametrize("references", ["not a list", [{}] * 6], ids=["not-list", "too-many"])
def test_related_notes_must_be_a_short_list(reader, references) -> None:
    with pytest.raises(ValueError, match="INVALID_ARGUMENT: related_notes must contain at most 5"):
        verified_related_notes(reader, references)


def test_unknown_related_note_names_a_truncated_handle(reader) -> None:
    with pytest.raises(ValueError, match="NOTE_UNAVAILABLE") as raised:
        verified_related_notes(reader, [{"note_id": "note_" + "x" * 100, "reason": "Related."}])
    message = str(raised.value)
    assert repr("note_" + "x" * 35) in message
    assert "x" * 36 not in message


@pytest.fixture
def suggest(vault_root, reader, notes, sample_source):
    """Call create_edit_suggestion against Notes/Retry.md with overridable arguments."""
    target = notes["Notes/Retry.md"]

    def call(**overrides):
        arguments = {
            "source": sample_source.metadata,
            "vault": reader,
            "target_note_id": target["note_id"],
            "target_revision": target["revision"],
            "change_summary": "Add a retry budget.",
            "reasoning": "The note omits aggregate load.",
            "proposed_content": "A retry budget caps retries.",
        } | overrides
        return create_edit_suggestion(DraftWriterSettings(vault_root), **arguments)

    return call


def test_edit_suggestion_links_the_unchanged_target(suggest, vault_root) -> None:
    from obsidian_ingest.extractor.providers import Citation

    result = suggest(citation=Citation("Relevant timestamps", "[01:05](https://example.test)"))
    text = result.path.read_text(encoding="utf-8")
    assert result.path.name == "edit-retry.md"
    assert text.startswith(
        "[[Notes/Retry.md]]\n\n## Suggested Change\n\nAdd a retry budget.\n\n"
        "## Why\n\nThe note omits aggregate load.\n\n"
        "## Proposed Content\n\nA retry budget caps retries.\n\n## Source"
    )
    assert "[01:05]" in text
    assert (vault_root / "Notes/Retry.md").read_text(encoding="utf-8") == (
        "Retry budgets limit load."
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"change_summary": ""}, "change_summary must be nonempty"),
        ({"change_summary": "  "}, "change_summary must be nonempty"),
        ({"change_summary": "x" * 501}, "change_summary must be nonempty"),
        ({"change_summary": 5}, "change_summary must be nonempty"),
        ({"change_summary": "one\ntwo"}, "change_summary must be one line"),
        ({"change_summary": "one\rtwo"}, "change_summary must be one line"),
        ({"reasoning": ""}, "reasoning must be nonempty"),
        ({"reasoning": "x" * 3001}, "reasoning must be nonempty"),
        ({"reasoning": None}, "reasoning must be nonempty"),
        ({"reasoning": "one\n\ntwo"}, "reasoning must be a single paragraph"),
        ({"reasoning": "one\rtwo"}, "reasoning must be a single paragraph"),
        ({"proposed_content": " \n "}, "proposed_content must be nonempty"),
        ({"proposed_content": "x" * 20_001}, "proposed_content must be nonempty"),
        ({"proposed_content": b"bytes"}, "proposed_content must be nonempty"),
    ],
)
def test_invalid_edit_suggestion_text_writes_nothing(
    suggest, vault_root, overrides, message
) -> None:
    with pytest.raises(ValueError, match=f"INVALID_ARGUMENT: {message}"):
        suggest(**overrides)
    assert _drafts(vault_root) == []


def test_edit_suggestion_target_must_be_current_available_and_linkable(
    suggest, vault_root, notes
) -> None:
    unlinkable = notes["Notes/Odd#Name.md"]
    cases = [
        ({"target_note_id": "note_unknown"}, "NOTE_UNAVAILABLE"),
        (
            {"target_note_id": unlinkable["note_id"], "target_revision": unlinkable["revision"]},
            "NOTE_NOT_LINKABLE",
        ),
    ]
    for overrides, code in cases:
        with pytest.raises(ValueError, match=f"^{code}: "):
            suggest(**overrides)
    (vault_root / "Notes/Retry.md").write_text("A human edited this.", encoding="utf-8")
    with pytest.raises(ValueError, match="^NOTE_CHANGED: "):
        suggest()
    assert _drafts(vault_root) == []
    assert (vault_root / "Notes/Retry.md").read_text(encoding="utf-8") == "A human edited this."
