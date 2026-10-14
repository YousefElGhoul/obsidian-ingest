import json
from pathlib import Path

import pytest

from obsidian_ingest.config import VaultSettings, load_vault_settings
from obsidian_ingest.curation import verified_related_notes
from obsidian_ingest.vault import NoteChanged, NoteUnavailable, VaultReader


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Engineering").mkdir()
    (root / "Computer Science").mkdir()
    (root / "Private").mkdir()
    (root / "00 Inbox/AI Drafts").mkdir(parents=True)
    (root / "Engineering/Retry Policies.md").write_text(
        "Retries amplify outage load. A retry budget limits amplification.\n", encoding="utf-8"
    )
    (root / "Computer Science/Retry Policies.md").write_text(
        "A separate note about algorithms.", encoding="utf-8"
    )
    (root / "Private/Secret.md").write_text("SECRET_DO_NOT_LEAK", encoding="utf-8")
    (root / "00 Inbox/AI Drafts/Previous.md").write_text("An earlier draft.", encoding="utf-8")
    return root


def _settings(root: Path, allowed=("*",), excluded=("Private",)) -> VaultSettings:
    return VaultSettings(root, allowed, excluded)


def test_wildcard_search_obeys_exclusions_including_draft_notes(tmp_path):
    root = _vault(tmp_path)
    with VaultReader(_settings(root)) as reader:
        result = reader.search("retry", limit=10)
        assert [note["path"] for note in result["notes"]] == [
            "Computer Science/Retry Policies.md",
            "Engineering/Retry Policies.md",
        ]
        assert result["notes"][1]["snippet"].find("retry") >= 0
        browse_paths = {note["path"] for note in reader.search("", limit=20)["notes"]}
        assert "Private/Secret.md" not in browse_paths
        assert "00 Inbox/AI Drafts/Previous.md" in browse_paths
        drafts = reader.search("earlier", scope="content")
        assert drafts["notes"][0]["path"] == "00 Inbox/AI Drafts/Previous.md"
        assert "SECRET_DO_NOT_LEAK" not in json.dumps(result)
        assert "Private/Secret.md" not in json.dumps(result)


def test_explicit_allowances_and_exclusions_use_path_components(tmp_path):
    root = _vault(tmp_path)
    (root / "Engineering/Private").mkdir()
    (root / "Engineering/Private/Hidden.md").write_text("excluded descendant", encoding="utf-8")
    (root / "Engineeringering").mkdir()
    (root / "Engineeringering/Visible.md").write_text("allowed sibling", encoding="utf-8")
    with VaultReader(
        _settings(
            root,
            ("Engineering", "Engineeringering", "00 Inbox/AI Drafts"),
            ("Engineering/Private",),
        )
    ) as reader:
        results = reader.search("")
        paths = {item["path"] for item in results["notes"]}
        assert "Engineering/Retry Policies.md" in paths
        assert "Engineeringering/Visible.md" in paths
        assert "Engineering/Private/Hidden.md" not in paths
        assert "Computer Science/Retry Policies.md" not in paths
        assert "Private/Secret.md" not in paths
        assert "00 Inbox/AI Drafts/Previous.md" in paths


def test_search_pagination_cursor_and_note_revision_reads(tmp_path):
    root = _vault(tmp_path)
    with VaultReader(_settings(root)) as reader:
        first = reader.search("retry", limit=1)
        assert len(first["notes"]) == 1
        assert first["next_cursor"]
        second = reader.search("retry", limit=1, cursor=first["next_cursor"])
        assert len(second["notes"]) == 1
        assert second["notes"][0]["path"] != first["notes"][0]["path"]
        with pytest.raises(ValueError, match="INVALID_CURSOR"):
            reader.search("retry", scope="title", limit=1, cursor=first["next_cursor"])
        note = first["notes"][0]
        page = reader.read(note["note_id"], limit=8)
        assert page["text"]
        assert page["next_offset"] == 8
        next_page = reader.read(
            note["note_id"], offset=page["next_offset"], limit=8, revision=page["revision"]
        )
        assert next_page["offset"] == 8
        (root / note["path"]).write_text("changed", encoding="utf-8")
        with pytest.raises(NoteChanged):
            reader.read(
                note["note_id"], offset=next_page["next_offset"] or 0, revision=page["revision"]
            )


def test_verified_related_references_deduplicate_paths_not_only_handles(tmp_path):
    root = _vault(tmp_path)
    with VaultReader(_settings(root)) as reader:
        result_a = reader.search("retry", scope="title", limit=2)
        result_b = reader.search("retry", scope="title", limit=2)
        first_a = result_a["notes"][0]
        first_b = result_b["notes"][0]
        assert first_a["note_id"] != first_b["note_id"]
        (root / first_a["path"]).write_text("A harmless edit after search.", encoding="utf-8")
        verified = verified_related_notes(
            reader,
            [
                {"note_id": item["note_id"], "reason": "Useful context."}
                for item in (first_a, first_b)
            ],
        )
        assert len(verified) == 1
        assert verified[0][0] == f"[[{first_a['path']}]]"


def test_unknown_excluded_and_traversal_references_share_generic_error(tmp_path):
    root = _vault(tmp_path)
    with VaultReader(_settings(root)) as reader:
        for note_id in ("unknown", "../Private/Secret.md", "note_fake"):
            with pytest.raises(NoteUnavailable):
                reader.get_reference(note_id)


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_symlinks_are_not_listed_or_followed(tmp_path):
    root = _vault(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "Secret.md").write_text("outside", encoding="utf-8")
    (root / "Escape").symlink_to(outside, target_is_directory=True)
    (root / "Linked.md").symlink_to(outside / "Secret.md")
    with VaultReader(_settings(root)) as reader:
        output = json.dumps(reader.search(""))
        assert "Escape" not in output
        assert "Linked.md" not in output
        assert "Secret.md" not in output


def test_hard_link_aliases_are_not_exposed(tmp_path):
    root = _vault(tmp_path)
    (root / "Alias.md").hardlink_to(root / "Engineering/Retry Policies.md")
    with VaultReader(_settings(root)) as reader:
        paths = {item["path"] for item in reader.search("retry")["notes"]}
        assert "Alias.md" not in paths
        assert "Engineering/Retry Policies.md" not in paths


def test_large_notes_are_read_in_bounded_revision_checked_pages(tmp_path):
    root = _vault(tmp_path)
    content = "word " * 20_000
    (root / "Large.md").write_text(content, encoding="utf-8")
    with VaultReader(_settings(root)) as reader:
        note = next(
            item for item in reader.search("word", limit=20)["notes"] if item["path"] == "Large.md"
        )
        pages = []
        offset = 0
        revision = note["revision"]
        while True:
            page = reader.read(note["note_id"], offset=offset, limit=8000, revision=revision)
            assert len(page["text"]) <= 8000
            pages.append(page["text"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
        assert "".join(pages) == content


def test_content_search_matches_terms_crossing_stream_chunks(tmp_path):
    root = _vault(tmp_path)
    content = "x" * (64 * 1024 - len("boundary ")) + "boundary phrase across a chunk boundary"
    (root / "Chunk Boundary.md").write_text(content, encoding="utf-8")
    with VaultReader(_settings(root)) as reader:
        results = reader.search("boundary phrase", scope="content")
        assert [item["path"] for item in results["notes"]] == ["Chunk Boundary.md"]
        assert "boundary" in results["notes"][0]["snippet"]


def test_configuration_validates_paths_scope_and_draft_destination(tmp_path):
    root = _vault(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(
        f'[vault]\nroot = "{root}"\nallowed = ["*"]\nexcluded = ["Private"]\n',
        encoding="utf-8",
    )
    settings = load_vault_settings(config)
    assert settings.allowed == ("*",)
    with VaultReader(settings):
        pass

    config.write_text(
        f'[vault]\nroot = "{root}"\nallowed = ["Engineering"]\nexcluded = ["Engineering"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Allowed path is missing or unsafe"):
        VaultReader(load_vault_settings(config))

    config.write_text(
        f'[vault]\nroot = "{root}"\nallowed = ["*"]\nexcluded = ["00 Inbox"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Draft destination"):
        VaultReader(load_vault_settings(config))


@pytest.mark.parametrize(
    "allowed,extra",
    [
        ('["*", "Engineering"]', ""),
        ('["/tmp/private"]', ""),
        ('["../outside"]', ""),
        ('["*"]', "typo = true"),
        ("[]", ""),
    ],
)
def test_configuration_rejects_malformed_or_unsafe_settings(tmp_path, allowed, extra):
    root = _vault(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(f'[vault]\nroot = "{root}"\nallowed = {allowed}\n{extra}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_vault_settings(config)


def test_note_read_requires_revision_for_continuation_and_bounds_result(tmp_path):
    root = _vault(tmp_path)
    (root / "Long.md").write_text("界" * 100_000, encoding="utf-8")
    with VaultReader(_settings(root)) as reader:
        note = next(
            item for item in reader.search("", limit=20)["notes"] if item["path"] == "Long.md"
        )
        with pytest.raises(ValueError, match="revision is required"):
            reader.read(note["note_id"], offset=1)
        page = reader.read(note["note_id"], limit=8000)
        encoded = json.dumps(
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(page, ensure_ascii=False, separators=(",", ":")),
                    }
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        assert len(encoded) <= 16 * 1024


def test_wildcard_may_use_not_yet_created_draft_folder(tmp_path):
    root = _vault(tmp_path)
    settings = VaultSettings(root, ("*",), ("Private",), "New Drafts/AI")
    with VaultReader(settings):
        pass


def test_explicitly_allowed_future_draft_folder_is_the_only_missing_allowance_exception(tmp_path):
    root = _vault(tmp_path)
    draft = root / "00 Inbox/AI Drafts"
    (draft / "Previous.md").unlink()
    draft.rmdir()
    with VaultReader(VaultSettings(root, ("00 Inbox/AI Drafts",), ("Private",))):
        pass
    assert not draft.exists()
    with pytest.raises(ValueError, match="Allowed path is missing or unsafe"):
        VaultReader(VaultSettings(root, ("Future Notes",), ("Private",)))


@pytest.mark.parametrize(
    "unsafe_name",
    ["Heading#Section", "Block^Reference", "Alias|Name", "Colon:Note", "Comment%%Note", "Bracket["],
)
def test_special_wikilink_paths_are_searchable_but_never_linkable(tmp_path, unsafe_name):
    root = _vault(tmp_path)
    path = root / f"Engineering/{unsafe_name}.md"
    path.write_text("A readable note.", encoding="utf-8")
    with VaultReader(_settings(root)) as reader:
        note = next(
            item
            for item in reader.search("readable")["notes"]
            if item["path"] == f"Engineering/{unsafe_name}.md"
        )
        assert note["linkable"] is False
        with pytest.raises(ValueError, match="NOTE_NOT_LINKABLE"):
            verified_related_notes(
                reader,
                [
                    {
                        "note_id": note["note_id"],
                        "revision": note["revision"],
                        "reason": "Related concept.",
                    }
                ],
            )


def test_large_vault_search_pages_without_duplicates(tmp_path):
    root = _vault(tmp_path)
    folder = root / "Many"
    folder.mkdir()
    for index in range(47):
        (folder / f"Note {index:02}.md").write_text("bounded search fixture", encoding="utf-8")
    with VaultReader(_settings(root)) as reader:
        paths = []
        cursor = None
        while True:
            result = reader.search("fixture", scope="content", cursor=cursor, limit=7)
            paths.extend(note["path"] for note in result["notes"])
            cursor = result["next_cursor"]
            if cursor is None:
                break
        matching = [path for path in paths if path.startswith("Many/")]
        assert matching == [f"Many/Note {index:02}.md" for index in range(47)]
        assert len(paths) == len(set(paths))


def test_draft_destination_symlink_is_rejected_at_startup(tmp_path):
    root = _vault(tmp_path)
    destination = root / "00 Inbox/AI Drafts"
    (destination / "Previous.md").unlink()
    destination.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    destination.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="Draft destination"):
        VaultReader(_settings(root))


def test_excluded_archive_inside_draft_destination_is_allowed_but_not_searchable(tmp_path):
    root = _vault(tmp_path)
    archive = root / "00 Inbox/AI Drafts/Archive"
    archive.mkdir()
    (archive / "Secret Archive.md").write_text("ARCHIVE_CONTENT", encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        f'[vault]\nroot = "{root}"\nallowed = ["*"]\nexcluded = ["00 Inbox/AI Drafts/Archive"]\n',
        encoding="utf-8",
    )
    with VaultReader(load_vault_settings(config)) as reader:
        serialized = json.dumps(reader.search(""))
        assert "00 Inbox/AI Drafts/Previous.md" in serialized
        assert "Archive" not in serialized
        assert "ARCHIVE_CONTENT" not in serialized


def test_inaccessible_draft_destination_is_rejected(monkeypatch, tmp_path):
    root = _vault(tmp_path)
    monkeypatch.setattr("obsidian_ingest.vault.os.access", lambda *args, **kwargs: False)
    with pytest.raises(ValueError, match="not writable"):
        VaultReader(_settings(root))


def test_direct_settings_reject_path_traversal_and_wildcard_conflicts(tmp_path):
    root = _vault(tmp_path)
    for settings in (
        VaultSettings(root, ("../outside",), ()),
        VaultSettings(root, ("*", "Engineering"), ()),
        VaultSettings(root, ("*",), (), "../outside"),
    ):
        with pytest.raises(ValueError):
            VaultReader(settings)
