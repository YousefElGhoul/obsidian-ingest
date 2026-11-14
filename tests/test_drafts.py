import errno
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from obsidian_ingest import drafts
from obsidian_ingest.drafts import DraftWriterSettings, create_draft
from obsidian_ingest.extractor import Provider
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


def test_create_draft_names_file_from_h1_and_keeps_h1_verbatim(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    contents = '# A "useful" idea: why?\n\nAn explanation with **formatting**.'
    result = create_draft(
        settings,
        contents=contents,
        source=source_metadata,
        timestamps=(12.5, 45),
    )

    assert result.path == settings.vault_path / "00 Inbox/AI Drafts/a-useful-idea-why.md"
    assert result.path.read_text(encoding="utf-8") == (
        '# A "useful" idea: why?\n\n'
        "An explanation with **formatting**.\n\n"
        "## Source\n\n"
        "- Source: A useful source\n"
        "- URL: https://www.youtube.com/watch?v=video-123\n"
        "- Creator: A creator\n"
        "- Relevant timestamps: [00:12](https://www.youtube.com/watch?v=video-123&t=12s), "
        "[00:45](https://www.youtube.com/watch?v=video-123&t=45s)\n"
    )


def test_empty_body_and_missing_optional_metadata_are_supported(
    settings: DraftWriterSettings,
) -> None:
    source = normalize_youtube_metadata({}, "https://youtu.be/minimal")
    result = create_draft(settings, filename="Minimal", contents="", source=source)
    assert result.path.name == "minimal.md"
    assert result.path.read_text(encoding="utf-8") == (
        "## Source\n\n- Source: minimal\n- URL: https://youtu.be/minimal\n"
    )


def test_youtube_timestamp_links_use_video_id_and_floor_fractional_seconds(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    result = create_draft(
        settings,
        contents="# Linked timestamps\n\nBody",
        source=source_metadata,
        timestamps=(125.9, 3661.8),
    )
    rendered = result.path.read_text(encoding="utf-8")
    assert (
        "- Relevant timestamps: [02:05](https://www.youtube.com/watch?v=video-123&t=125s), "
        "[01:01:01](https://www.youtube.com/watch?v=video-123&t=3661s)" in rendered
    )


def test_youtube_timestamp_links_ignore_existing_url_query_parameters(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    from dataclasses import replace

    source = replace(
        source_metadata,
        original_url="https://www.youtube.com/watch?v=video-123&t=17s&list=playlist-1",
    )
    result = create_draft(
        settings, filename="Query parameters", contents="Body", source=source, timestamps=(125,)
    )
    rendered = result.path.read_text(encoding="utf-8")
    assert "[02:05](https://www.youtube.com/watch?v=video-123&t=125s)" in rendered
    assert "&t=17s&t=" not in rendered
    assert "- URL: https://www.youtube.com/watch?v=video-123&t=17s&list=playlist-1" in rendered


def test_non_youtube_timestamp_format_stays_plain(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    from dataclasses import replace

    source = replace(source_metadata, provider=Provider.TIKTOK)
    result = create_draft(
        settings, filename="Other provider", contents="Body", source=source, timestamps=(125.5,)
    )
    rendered = result.path.read_text(encoding="utf-8")
    assert "- Relevant timestamps: 125.5s" in rendered
    assert "[02:05]" not in rendered


def test_custom_relative_draft_folder_and_unicode_content(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    custom = DraftWriterSettings(settings.vault_path, "Inbox/Review Drafts")
    result = create_draft(
        custom,
        contents="# Café 方法\n\nÜberraschung.\n第二行。\n",
        source=source_metadata,
    )
    assert result.path == settings.vault_path / "Inbox/Review Drafts/café-方法.md"
    assert result.path.read_text(encoding="utf-8").startswith(
        "# Café 方法\n\nÜberraschung.\n第二行。\n\n## Source"
    )


@pytest.mark.parametrize(
    ("contents", "filename"),
    [
        ("# Postgres: One DB, Many Jobs?\n\nBody", "postgres-one-db-many-jobs.md"),
        ("# Café 方法\n\nBody", "café-方法.md"),
        ("\n\n  # Spaced   out ##\nBody", "spaced-out.md"),
        ("# Why `vibe` <coding> fails... (part 2)\nBody", "why-vibe-coding-fails-part-2.md"),
        ("# ../../etc/passwd\nBody", "etc-passwd.md"),
        ("# a/b\\c\x01d\nBody", "a-b-c-d.md"),
        ("# CON\nBody", "con-note.md"),
        ("# Nul\nBody", "nul-note.md"),
        ("# COM1\nBody", "com1-note.md"),
        ("# Top\n\n## Sub\n\nBody", "top.md"),
    ],
)
def test_filename_is_kebab_case_slug_of_leading_h1(
    settings: DraftWriterSettings, source_metadata: SourceMetadata, contents: str, filename: str
) -> None:
    result = create_draft(settings, contents=contents, source=source_metadata)
    assert result.path == settings.vault_path / "00 Inbox/AI Drafts" / filename
    assert result.path.read_text(encoding="utf-8").startswith(contents)


@pytest.mark.parametrize(
    ("given", "filename"),
    [
        ("My_File.md", "my-file.md"),
        ("My_File.MD", "my-file.md"),
        ("Plain name", "plain-name.md"),
        ("../x", "x.md"),
        ("a/b", "a-b.md"),
        ("..\\..\\outside", "outside.md"),
        ("notes.backup.md", "notes-backup.md"),
        ("con.md", "con-note.md"),
    ],
)
def test_given_filename_overrides_h1_and_stays_inside_draft_folder(
    settings: DraftWriterSettings, source_metadata: SourceMetadata, given: str, filename: str
) -> None:
    result = create_draft(
        settings, filename=given, contents="# Ignored title\n\nBody", source=source_metadata
    )
    assert result.path == settings.vault_path / "00 Inbox/AI Drafts" / filename
    assert result.path.read_text(encoding="utf-8").startswith("# Ignored title\n\nBody")


@pytest.mark.parametrize(
    ("contents", "filename"),
    [
        ("Body without a title", None),
        ("## Only an H2\n\nBody", None),
        ("Setext title\n============\n\nBody", None),
        ("#NoSpace\nBody", None),
        ("# !!!\nBody", None),
        ("# \nBody", None),
        ("Body", ""),
        ("Body", "..."),
        ("# Fine title\nBody", "///"),
    ],
)
def test_missing_h1_without_filename_or_empty_slug_is_rejected(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    contents: str,
    filename: str | None,
) -> None:
    with pytest.raises(ValueError, match="must start with an H1 title"):
        create_draft(settings, filename=filename, contents=contents, source=source_metadata)
    assert not (settings.vault_path / "00 Inbox").exists()


@pytest.mark.parametrize("title", ["a" * 300, "界" * 80, "ab " * 60])
def test_slug_stem_is_capped_at_120_bytes_on_a_character_boundary(
    settings: DraftWriterSettings, source_metadata: SourceMetadata, title: str
) -> None:
    result = create_draft(settings, contents=f"# {title}\n\nBody", source=source_metadata)
    stem = result.path.name.removesuffix(".md")
    assert len(stem.encode("utf-8")) <= 120
    assert stem and not stem.endswith("-")
    assert stem in "-".join(title.split())
    assert result.path.read_text(encoding="utf-8").startswith(f"# {title}\n\nBody")


def test_non_string_filename_is_rejected(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    with pytest.raises(TypeError, match="filename must be a string"):
        create_draft(settings, filename=3, contents="# T", source=source_metadata)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "contents",
    [
        "## SOURCE\nAgent attribution\n\n## Context\nMore",
        "## Sources\n- https://example.com\n\n## Later\nMore",
        "### Provenance\n- Claimed URL: https://example.com\n\n## Later\nMore",
        "Source Attribution\n-------------------\n- Claimed URL",
    ],
)
def test_mid_body_source_sections_are_rejected_before_filesystem_changes(
    settings: DraftWriterSettings, source_metadata: SourceMetadata, contents: str
) -> None:
    with pytest.raises(ValueError, match="Source or provenance"):
        create_draft(settings, contents=f"# Invalid body\n\n{contents}", source=source_metadata)
    assert not (settings.vault_path / "00 Inbox").exists()


def test_references_and_markdown_examples_are_allowed_and_source_is_appended_once(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    body = (
        "# Valid body\n\n"
        "A body with a source mention.\n\n"
        "## References\n- [Paper](https://example.com/paper)\n\n"
        "```markdown\n# Example title\n## Source\n```\n"
        "    ## Provenance\n"
    )
    result = create_draft(settings, contents=body, source=source_metadata)
    rendered = result.path.read_text(encoding="utf-8")
    assert rendered == (
        body
        + "\n## Source\n\n"
        + "- Source: A useful source\n"
        + "- URL: https://www.youtube.com/watch?v=video-123\n"
        + "- Creator: A creator\n"
    )
    assert rendered.count("## Source") == 2  # one fenced example, one generated section


def test_setext_h2_named_source_is_reserved(settings, source_metadata):
    with pytest.raises(ValueError, match="Source or provenance"):
        create_draft(
            settings,
            filename="Reserved",
            contents="Source\n------\nnot allowed",
            source=source_metadata,
        )
    assert not (settings.vault_path / "00 Inbox").exists()


def test_user_markdown_and_wikilinks_are_preserved(settings, source_metadata):
    body = (
        "# Expressive body\n\n"
        "An ordinary [Markdown link](https://example.com) and [[Existing Note]].\n\n"
        "`[[Inline example]]`\n\n"
        "```markdown\n[[Code example]]\n```"
    )
    result = create_draft(settings, contents=body, source=source_metadata)
    rendered = result.path.read_text(encoding="utf-8")
    assert rendered == body + (
        "\n\n## Source\n\n"
        "- Source: A useful source\n"
        "- URL: https://www.youtube.com/watch?v=video-123\n"
        "- Creator: A creator\n"
    )


def test_related_notes_heading_must_be_generated_from_verified_references(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    with pytest.raises(ValueError, match="Related Notes must be generated"):
        create_draft(
            settings,
            contents="# Unverified related heading\n\nA body.\n\n## Related Notes\n- [[Secret.md]]",
            source=source_metadata,
        )
    assert not (settings.vault_path / "00 Inbox").exists()


@pytest.mark.parametrize(
    "contents",
    [
        "A body.\n\n> ## Related Notes\n> - [[Unverified]]",
        "A body.\n\n> Related Notes\n> ----------------\n> [[Unverified]]",
    ],
)
def test_blockquoted_related_notes_heading_cannot_impersonate_generated_section(
    settings, source_metadata, contents
):
    with pytest.raises(ValueError, match="Related Notes must be generated"):
        create_draft(settings, filename="Quoted section", contents=contents, source=source_metadata)
    assert not (settings.vault_path / "00 Inbox").exists()


def test_related_links_must_be_safe_vault_relative_markdown_paths(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    with pytest.raises(ValueError, match="wikilink path is not safe"):
        create_draft(
            settings,
            contents="# Unsafe generated link\n\nA useful body.",
            source=source_metadata,
            related_notes=(("[[../Private/Secret.md]]", "Looks related."),),
        )
    assert not (settings.vault_path / "00 Inbox").exists()


def test_existing_files_are_preserved_and_collision_gets_numeric_suffix(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    folder = settings.vault_path / settings.draft_folder
    folder.mkdir(parents=True)
    original = folder / "same-title.md"
    original.write_text("Human-reviewed content", encoding="utf-8")
    second = folder / "same-title-2.md"
    second.write_text("Second human note", encoding="utf-8")

    result = create_draft(settings, contents="# Same title\n\nA new draft", source=source_metadata)

    assert result.path == folder / "same-title-3.md"
    assert original.read_text(encoding="utf-8") == "Human-reviewed content"
    assert second.read_text(encoding="utf-8") == "Second human note"
    assert result.path.read_text(encoding="utf-8").startswith("# Same title\n\nA new draft\n\n")


@pytest.mark.parametrize("collision_index", [2, 10])
@pytest.mark.parametrize("title", ["a" * 300, "界" * 80])
def test_maximum_stem_collision_suffix_stays_within_component_limit(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    collision_index: int,
    title: str,
) -> None:
    folder = settings.vault_path / settings.draft_folder
    folder.mkdir(parents=True)
    first = create_draft(settings, contents=f"# {title}", source=source_metadata)
    stem = first.path.name.removesuffix(".md")
    existing = {first.path: first.path.read_text(encoding="utf-8")}
    for index in range(2, collision_index):
        path = folder / f"{stem}-{index}.md"
        contents = f"Preserve existing note {index}"
        path.write_text(contents, encoding="utf-8")
        existing[path] = contents

    result = create_draft(settings, contents=f"# {title}\n\nNew", source=source_metadata)

    assert result.path.name == f"{stem}-{collision_index}.md"
    assert len(os.fsencode(result.path.name)) <= 255
    assert {path: path.read_text(encoding="utf-8") for path in existing} == existing


def test_a_leading_h2_and_later_h1_headings_are_kept(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    first = create_draft(
        settings, filename="A", contents="## Summary\n\nBody", source=source_metadata
    )
    assert first.path.read_text(encoding="utf-8").startswith("## Summary\n\nBody")
    later = create_draft(
        settings, filename="B", contents="Intro.\n\n# Later heading\n", source=source_metadata
    )
    assert "# Later heading" in later.path.read_text(encoding="utf-8")


def test_listed_timestamps_collapse_to_distinct_moments(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    cited = tuple(float(second) for second in range(317, 420, 2)) + (5.0, 600.0)
    result = create_draft(
        settings, contents="# Moments\n\nBody.", source=source_metadata, timestamps=cited
    )
    listed = result.path.read_text(encoding="utf-8").split("Relevant timestamps: ")[1]
    assert [link.split("]")[0] for link in listed.split("[")[1:]] == [
        "00:05",
        "05:17",
        "05:47",
        "06:17",
        "06:47",
        "10:00",
    ]


def test_literal_backslash_n_instead_of_line_breaks_is_rejected(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    with pytest.raises(ValueError, match="real line"):
        create_draft(settings, contents="# Title\\n\\nOne paragraph.", source=source_metadata)
    kept = create_draft(
        settings,
        contents="# Escapes\n\nA regex like `\\n` in prose is fine.",
        source=source_metadata,
    )
    assert "`\\n`" in kept.path.read_text(encoding="utf-8")


def test_a_draft_with_several_h1_titles_is_rejected_as_several_ideas(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    contents = "# First idea\n\nBody.\n\n# Second idea\n\nMore.\n\n```\n# not a heading\n```\n"
    with pytest.raises(ValueError, match="one idea"):
        create_draft(settings, contents=contents, source=source_metadata)
    fenced = create_draft(
        settings, contents="# Only idea\n\n```sh\n# a shell comment\n```\n", source=source_metadata
    )
    assert fenced.path.name == "only-idea.md"


@pytest.mark.parametrize(
    "contents",
    [
        "# T\n\nBody.\n\n## Source\n- https://example.com",
        "# T\n\nBody.\n\n---\n\n## Sources\n\nVideo by someone.\n",
        "# T\n\nBody.\n\n### provenance ###\n- Claimed URL",
    ],
)
def test_a_final_agent_source_section_is_replaced_by_verified_provenance(
    settings: DraftWriterSettings, source_metadata: SourceMetadata, contents: str
) -> None:
    result = create_draft(settings, contents=contents, source=source_metadata)
    text = result.path.read_text(encoding="utf-8")
    assert text.startswith("# T\n\nBody.\n\n## Source\n")
    assert text.count("## Source") == 1
    assert "example.com" not in text and "Claimed URL" not in text


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
        create_draft(unsafe_settings, contents="# A title\n\nbody", source=source_metadata)


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
        create_draft(settings, contents="# Unsafe\n\nbody", source=source_metadata)
    assert not (outside / "unsafe.md").exists()


def test_missing_vault_is_not_created(tmp_path: Path, source_metadata: SourceMetadata) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        create_draft(
            DraftWriterSettings(missing),
            contents="# Draft\n\nbody",
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
            contents="# Invalid timestamp\n\nbody",
            source=source_metadata,
            timestamps=timestamps,
        )


def test_timestamps_must_be_tuple_of_numbers(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    with pytest.raises(TypeError, match="timestamps must be a tuple"):
        create_draft(
            settings,
            contents="# List timestamps\n\nbody",
            source=source_metadata,
            timestamps=[1.0],  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="timestamps must be numbers"):
        create_draft(
            settings,
            contents="# Invalid timestamp type\n\nbody",
            source=source_metadata,
            timestamps=(True,),
        )


def test_existing_directory_with_draft_filename_is_not_replaced(
    settings: DraftWriterSettings, source_metadata: SourceMetadata
) -> None:
    folder = settings.vault_path / settings.draft_folder
    folder.mkdir(parents=True)
    existing_directory = folder / "a-title.md"
    existing_directory.mkdir()

    result = create_draft(settings, contents="# A title\n\nbody", source=source_metadata)

    assert result.path == folder / "a-title-2.md"
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
    (folder / "a-title.md").symlink_to(outside)

    result = create_draft(settings, contents="# A title\n\nbody", source=source_metadata)

    assert result.path == folder / "a-title-2.md"
    assert outside.read_text(encoding="utf-8") == "Keep me"
    assert (folder / "a-title.md").is_symlink()


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
            (folder / "failure.md").write_text(replacement, encoding="utf-8")
            raise failure

        def flush(self):
            self.file_obj.flush()

        def fileno(self):
            return self.file_obj.fileno()

    monkeypatch.setattr(drafts.os, "fdopen", lambda fd, mode: FailingWriter(real_fdopen(fd, mode)))

    with pytest.raises(type(failure)):
        create_draft(settings, contents="# Failure\n\nunfinished body", source=source_metadata)

    assert (folder / "failure.md").read_text(encoding="utf-8") == replacement
    assert not (folder / "failure-2.md").exists()
    assert tuple(folder.glob("*.md")) == (folder / "failure.md",)
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
        create_draft(settings, contents="# Failed\n\nbody", source=source_metadata)

    assert not tuple(folder.glob("*.md"))
    assert not tuple(folder.glob(".obsidian-ingest-*"))


def test_hidden_staging_names_are_not_markdown(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish = drafts._publish_staged_file

    def inspect_then_publish(directory_fd: int, staging_fd: int, stem: str) -> str:
        folder = settings.vault_path / settings.draft_folder
        staging_dirs = tuple(folder.glob(".obsidian-ingest-*"))
        assert len(staging_dirs) == 1
        staged_files = tuple(staging_dirs[0].iterdir())
        assert [path.name for path in staged_files] == [".draft.tmp"]
        assert all(not path.name.endswith(".md") for path in staging_dirs + staged_files)
        assert staged_files[0].read_text(encoding="utf-8").startswith("# Staged\n\nbody\n\n")
        return publish(directory_fd, staging_fd, stem)

    monkeypatch.setattr(drafts, "_publish_staged_file", inspect_then_publish)
    result = create_draft(settings, contents="# Staged\n\nbody", source=source_metadata)
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
    monkeypatch.setattr(
        drafts.os, "supports_dir_fd", set(drafts.os.supports_dir_fd) | {fail_staging_unlink}
    )
    result = create_draft(settings, contents="# Published\n\ncomplete", source=source_metadata)

    assert result.path.read_text(encoding="utf-8").startswith("# Published\n\ncomplete\n\n")
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

    def wait_then_publish(directory_fd: int, staging_fd: int, stem: str) -> str:
        both_staged.wait(timeout=5)
        return publish(directory_fd, staging_fd, stem)

    monkeypatch.setattr(drafts, "_publish_staged_file", wait_then_publish)

    def write(contents: str):
        return create_draft(
            settings, contents=f"# Concurrent\n\n{contents}", source=source_metadata
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(write, ("first", "second")))

    assert len({result.path for result in results}) == 2
    assert {result.path.name for result in results} == {"concurrent.md", "concurrent-2.md"}
    for result in results:
        assert result.path.read_text(encoding="utf-8").startswith(
            ("# Concurrent\n\nfirst", "# Concurrent\n\nsecond")
        )
    assert not tuple(results[0].path.parent.glob(".obsidian-ingest-*"))


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_DIRECTORY"])
def test_missing_safety_flag_fails_before_creating_draft_folder(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    monkeypatch.delattr(drafts.os, flag)

    with pytest.raises(RuntimeError, match=f"unavailable: {flag}"):
        create_draft(settings, contents="# Unsupported\n\nbody", source=source_metadata)

    assert not (settings.vault_path / "00 Inbox").exists()


@pytest.mark.parametrize(
    "operation",
    [drafts.os.open, drafts.os.mkdir, drafts.os.unlink, drafts.os.rmdir, drafts.os.link],
    ids=lambda operation: operation.__name__,
)
def test_missing_dir_fd_operation_fails_before_creating_draft_folder(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
    operation,
) -> None:
    supported = set(drafts.os.supports_dir_fd)
    supported.remove(operation)
    monkeypatch.setattr(drafts.os, "supports_dir_fd", supported)

    with pytest.raises(RuntimeError, match=f"unavailable: {operation.__name__}"):
        create_draft(settings, contents="# Unsupported\n\nbody", source=source_metadata)

    assert not (settings.vault_path / "00 Inbox").exists()


def test_missing_no_follow_link_support_fails_before_creating_draft_folder(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supported = set(drafts.os.supports_follow_symlinks)
    supported.discard(drafts.os.link)
    monkeypatch.setattr(drafts.os, "supports_follow_symlinks", supported)

    with pytest.raises(RuntimeError, match=r"link\(follow_symlinks=False\)"):
        create_draft(settings, contents="# Unsupported\n\nbody", source=source_metadata)

    assert not (settings.vault_path / "00 Inbox").exists()


def test_missing_fsync_fails_before_creating_draft_folder(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(drafts.os, "fsync")

    with pytest.raises(RuntimeError, match="unavailable: fsync"):
        create_draft(settings, contents="# Unsupported\n\nbody", source=source_metadata)

    assert not (settings.vault_path / "00 Inbox").exists()


def test_hard_link_publication_failure_does_not_publish_or_change_existing_notes(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = settings.vault_path / settings.draft_folder
    folder.mkdir(parents=True)
    existing = folder / "unavailable.md"
    existing.write_text("Preserve me", encoding="utf-8")

    def reject_hard_links(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, "hard links unsupported")

    monkeypatch.setattr(drafts.os, "link", reject_hard_links)
    monkeypatch.setattr(
        drafts.os, "supports_dir_fd", set(drafts.os.supports_dir_fd) | {reject_hard_links}
    )
    monkeypatch.setattr(
        drafts.os,
        "supports_follow_symlinks",
        set(drafts.os.supports_follow_symlinks) | {reject_hard_links},
    )

    with pytest.raises(OSError, match="could not atomically publish draft with a hard link"):
        create_draft(settings, contents="# Unavailable\n\nbody", source=source_metadata)

    assert existing.read_text(encoding="utf-8") == "Preserve me"
    assert tuple(folder.glob("*.md")) == (existing,)
    assert not tuple(folder.glob(".obsidian-ingest-*"))


def test_moving_open_draft_directory_can_publish_outside_configured_path(
    settings: DraftWriterSettings,
    source_metadata: SourceMetadata,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    publish = drafts._publish_staged_file
    configured_folder = settings.vault_path / settings.draft_folder
    moved_folder = tmp_path / "moved-drafts"

    def move_then_publish(directory_fd: int, staging_fd: int, stem: str) -> str:
        configured_folder.rename(moved_folder)
        return publish(directory_fd, staging_fd, stem)

    monkeypatch.setattr(drafts, "_publish_staged_file", move_then_publish)
    result = create_draft(settings, contents="# Moved\n\ncomplete", source=source_metadata)

    assert result.path == configured_folder / "moved.md"
    assert not result.path.exists()
    assert (moved_folder / "moved.md").read_text(encoding="utf-8").startswith("# Moved\n\ncomplete")
    assert not tuple(moved_folder.glob(".obsidian-ingest-*"))


def test_writer_requires_existing_directory_root(
    tmp_path: Path, source_metadata: SourceMetadata
) -> None:
    file_path = tmp_path / "not-a-vault"
    file_path.write_text("not a directory", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        create_draft(
            DraftWriterSettings(file_path),
            contents="# Draft\n\nbody",
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
        create_draft(custom, contents="# Unsafe\n\nbody", source=source_metadata)
    assert not (outside / "Drafts").exists()
