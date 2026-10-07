import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from obsidian_ingest import drafts
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


def test_existing_symlink_with_draft_filename_is_not_followed(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    tmp_path: Path,
) -> None:
    folder = settings.vault_path / settings.draft_folder
    folder.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("Keep me", encoding="utf-8")
    (folder / "A title.md").symlink_to(outside)

    result = create_draft(settings, title="A title", contents="body", source=source_metadata)

    assert result.path == folder / "A title (2).md"
    assert outside.read_text(encoding="utf-8") == "Keep me"
    assert (folder / "A title.md").is_symlink()


@pytest.mark.parametrize(
    "failure",
    [OSError("simulated write failure"), KeyboardInterrupt("simulated interruption")],
)
def test_write_failure_or_interruption_leaves_replacement_untouched_and_no_draft(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    folder = settings.vault_path / settings.draft_folder
    replacement = "another process's file"
    real_fdopen = drafts.os.fdopen

    class FailingWriter:
        def __init__(self, file_obj):
            self.file_obj = file_obj

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file_obj.close()

        def write(self, contents):
            self.file_obj.write(contents[:4])
            (folder / "Failure.md").write_text(replacement, encoding="utf-8")
            raise failure

        def flush(self):
            self.file_obj.flush()

        def fileno(self):
            return self.file_obj.fileno()

    monkeypatch.setattr(drafts.os, "fdopen", lambda fd, mode: FailingWriter(real_fdopen(fd, mode)))

    with pytest.raises(type(failure)):
        create_draft(settings, title="Failure", contents="unfinished body", source=source_metadata)

    assert (folder / "Failure.md").read_text(encoding="utf-8") == replacement
    assert not (folder / "Failure (2).md").exists()
    assert tuple(folder.glob("*.md")) == (folder / "Failure.md",)
    assert not tuple(folder.glob(".obsidian-ingest-*/.draft.tmp"))
    assert not tuple(folder.glob(".obsidian-ingest-*"))


@pytest.mark.parametrize("failure_point", ["flush", "close"])
def test_flush_or_close_failure_does_not_publish_markdown(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    folder = settings.vault_path / settings.draft_folder
    real_fdopen = drafts.os.fdopen

    class FailingWriter:
        def __init__(self, file_obj):
            self.file_obj = file_obj

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file_obj.close()
            if failure_point == "close":
                raise OSError("simulated close failure")

        def write(self, contents):
            return self.file_obj.write(contents)

        def flush(self):
            if failure_point == "flush":
                raise OSError("simulated flush failure")
            self.file_obj.flush()

        def fileno(self):
            return self.file_obj.fileno()

    monkeypatch.setattr(drafts.os, "fdopen", lambda fd, mode: FailingWriter(real_fdopen(fd, mode)))

    with pytest.raises(OSError, match=f"simulated {failure_point} failure"):
        create_draft(settings, title="Failed", contents="body", source=source_metadata)

    assert not tuple(folder.glob("*.md"))
    assert not tuple(folder.glob(".obsidian-ingest-*"))


def test_hidden_staging_names_are_not_markdown(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish = drafts._publish_staged_file

    def inspect_then_publish(directory_fd: int, staging_fd: int, title: str) -> str:
        folder = settings.vault_path / settings.draft_folder
        staging_dirs = tuple(folder.glob(".obsidian-ingest-*"))
        assert len(staging_dirs) == 1
        staged_files = tuple(staging_dirs[0].iterdir())
        assert [path.name for path in staged_files] == [".draft.tmp"]
        assert all(not path.name.endswith(".md") for path in staging_dirs + staged_files)
        assert staged_files[0].read_text(encoding="utf-8").startswith("body\n\n## Source")
        return publish(directory_fd, staging_fd, title)

    monkeypatch.setattr(drafts, "_publish_staged_file", inspect_then_publish)
    result = create_draft(settings, title="Staged", contents="body", source=source_metadata)
    assert result.path.is_file()
    assert not tuple(result.path.parent.glob(".obsidian-ingest-*"))


def test_staging_cleanup_failure_after_publication_does_not_fail_success(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_unlink = drafts.os.unlink

    def fail_staging_unlink(path, *args, **kwargs):
        if path == ".draft.tmp":
            raise PermissionError("simulated staging cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(drafts.os, "unlink", fail_staging_unlink)
    result = create_draft(settings, title="Published", contents="complete", source=source_metadata)

    assert result.path.read_text(encoding="utf-8").startswith("complete\n\n## Source")
    staging_dirs = tuple(result.path.parent.glob(".obsidian-ingest-*"))
    assert len(staging_dirs) == 1
    assert (staging_dirs[0] / ".draft.tmp").exists()


def test_concurrent_same_title_writes_publish_distinct_complete_files(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish = drafts._publish_staged_file
    both_staged = Barrier(2)

    def wait_then_publish(directory_fd: int, staging_fd: int, title: str) -> str:
        both_staged.wait(timeout=5)
        return publish(directory_fd, staging_fd, title)

    monkeypatch.setattr(drafts, "_publish_staged_file", wait_then_publish)

    def write(contents: str):
        return create_draft(settings, title="Concurrent", contents=contents, source=source_metadata)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(write, ("first", "second")))

    assert len({result.path for result in results}) == 2
    assert {result.path.name for result in results} == {"Concurrent.md", "Concurrent (2).md"}
    for result in results:
        assert result.path.read_text(encoding="utf-8").startswith(("first", "second"))
    assert not tuple(results[0].path.parent.glob(".obsidian-ingest-*"))


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
