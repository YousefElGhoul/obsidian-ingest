import os
from pathlib import Path

import pytest

from obsidian_ingest.drafts import DraftWriterSettings, create_draft
from obsidian_ingest.extractor.metadata import SourceMetadata
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata


@pytest.fixture
def source_metadata() -> SourceMetadata:
    return normalize_youtube_metadata(
        {
            "id": "video-123",
            "title": "A useful source",
            "channel": "A creator",
        },
        "https://www.youtube.com/watch?v=video-123",
    )


@pytest.fixture
def settings(tmp_path: Path) -> DraftWriterSettings:
    vault = tmp_path / "vault"
    vault.mkdir()
    return DraftWriterSettings(vault)


def test_create_draft_creates_default_nested_destination(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    result = create_draft(
        settings,
        title="A useful idea",
        contents="An explanation with **formatting**.",
        source=source_metadata,
        timestamps=(12.5, 45),
    )

    assert result.path == settings.vault_path / "00 Inbox/AI Drafts/A useful idea.md"
    assert result.path.read_text(encoding="utf-8") == (
        "An explanation with **formatting**.\n\n"
        "## Source\n\n"
        "- Source: A useful source\n"
        "- URL: https://www.youtube.com/watch?v=video-123\n"
        "- Creator: A creator\n"
        "- Relevant timestamps: 12.5s, 45s\n"
    )
    assert "# A useful idea" not in result.path.read_text(encoding="utf-8")


def test_empty_body_and_missing_optional_metadata_are_supported(
    settings: DraftWriterSettings,
) -> None:
    source = normalize_youtube_metadata({}, "https://youtu.be/minimal")
    result = create_draft(settings, title="Minimal", contents="", source=source)
    assert result.path.read_text(encoding="utf-8") == (
        "## Source\n\n- Source: minimal\n- URL: https://youtu.be/minimal\n"
    )


def test_custom_relative_draft_folder_and_unicode_content(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    custom = DraftWriterSettings(settings.vault_path, "Inbox/Review Drafts")
    result = create_draft(
        custom,
        title="Café 方法",
        contents="Überraschung.\n第二行。\n",
        source=source_metadata,
    )
    assert result.path == settings.vault_path / "Inbox/Review Drafts/Café 方法.md"
    assert result.path.read_text(encoding="utf-8").startswith(
        "Überraschung.\n第二行。\n\n## Source"
    )


def test_existing_files_are_preserved_and_collision_gets_numeric_suffix(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    folder = settings.vault_path / settings.draft_folder
    folder.mkdir(parents=True)
    original = folder / "Same title.md"
    original.write_text("Human-reviewed content", encoding="utf-8")

    result = create_draft(
        settings, title="Same title", contents="A new draft", source=source_metadata
    )

    assert result.path == folder / "Same title (2).md"
    assert original.read_text(encoding="utf-8") == "Human-reviewed content"
    assert result.path.read_text(encoding="utf-8").startswith("A new draft\n\n## Source")


@pytest.mark.parametrize(
    "title",
    [
        "",
        "   ",
        " padded ",
        ".",
        "..",
        "../outside",
        "nested/title",
        "bad:name",
        "trailing.",
        "CON",
        "NUL.txt",
        "x" * 241,
    ],
)
def test_unsafe_or_nonportable_titles_are_rejected(
    settings: DraftWriterSettings, source_metadata: SourceMetadata, title: str
) -> None:
    with pytest.raises(ValueError, match="title"):
        create_draft(settings, title=title, contents="body", source=source_metadata)
    assert not (settings.vault_path / "00 Inbox").exists()


@pytest.mark.parametrize(
    "folder",
    [
        "",
        ".",
        "../outside",
        "/tmp/outside",
        "Inbox/../../outside",
        r"Inbox\Drafts",
        "trailing./Drafts",
    ],
)
def test_unsafe_draft_folders_are_rejected(
    settings: DraftWriterSettings, source_metadata: SourceMetadata, folder: str
) -> None:
    unsafe_settings = DraftWriterSettings(settings.vault_path, folder)
    with pytest.raises(ValueError, match="draft_folder"):
        create_draft(unsafe_settings, title="A title", contents="body", source=source_metadata)


def test_symlink_in_draft_folder_cannot_escape_vault(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (settings.vault_path / "00 Inbox").mkdir()
    (settings.vault_path / "00 Inbox/AI Drafts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        create_draft(settings, title="Unsafe", contents="body", source=source_metadata)
    assert not (outside / "Unsafe.md").exists()


def test_missing_vault_is_not_created(tmp_path: Path, source_metadata: SourceMetadata) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        create_draft(
            DraftWriterSettings(missing),
            title="Draft",
            contents="body",
            source=source_metadata,
        )
    assert not missing.exists()


@pytest.mark.parametrize("timestamps", [(-1,), (float("nan"),), (float("inf"),)])
def test_invalid_timestamps_are_rejected(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    timestamps: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError, match="timestamps must be finite and nonnegative"):
        create_draft(
            settings,
            title="Invalid timestamp",
            contents="body",
            source=source_metadata,
            timestamps=timestamps,
        )


def test_timestamps_must_be_tuple_of_numbers(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    with pytest.raises(TypeError, match="timestamps must be a tuple"):
        create_draft(
            settings,
            title="List timestamps",
            contents="body",
            source=source_metadata,
            timestamps=[1.0],  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="timestamps must be numbers"):
        create_draft(
            settings,
            title="Invalid timestamp type",
            contents="body",
            source=source_metadata,
            timestamps=(True,),
        )


def test_existing_directory_with_draft_filename_is_not_replaced(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    folder = settings.vault_path / settings.draft_folder
    folder.mkdir(parents=True)
    existing_directory = folder / "A title.md"
    existing_directory.mkdir()

    result = create_draft(settings, title="A title", contents="body", source=source_metadata)

    assert result.path == folder / "A title (2).md"
    assert existing_directory.is_dir()


def test_writer_requires_existing_directory_root(
    tmp_path: Path, source_metadata: SourceMetadata
) -> None:
    file_path = tmp_path / "not-a-vault"
    file_path.write_text("not a directory", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        create_draft(
            DraftWriterSettings(file_path),
            title="Draft",
            contents="body",
            source=source_metadata,
        )


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks are unavailable")
def test_symlink_component_in_relative_folder_is_not_followed(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (settings.vault_path / "Linked").symlink_to(outside, target_is_directory=True)
    custom = DraftWriterSettings(settings.vault_path, "Linked/Drafts")

    with pytest.raises(OSError):
        create_draft(custom, title="Unsafe", contents="body", source=source_metadata)
    assert not (outside / "Drafts").exists()
