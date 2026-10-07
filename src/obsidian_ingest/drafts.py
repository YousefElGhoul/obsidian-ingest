"""Safe creation of provenance-preserving Markdown drafts."""

import os
import re
import stat
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from obsidian_ingest.extractor.metadata import SourceMetadata

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True, slots=True)
class DraftWriterSettings:
    """Trusted destination configuration; the agent supplies no filesystem paths."""

    vault_path: Path
    draft_folder: str = "00 Inbox/AI Drafts"


@dataclass(frozen=True, slots=True)
class DraftResult:
    path: Path


def _draft_folder_parts(folder: str) -> tuple[str, ...]:
    if not isinstance(folder, str) or not folder or "\\" in folder:
        raise ValueError("draft_folder must be a nonempty relative vault path")
    parts = tuple(folder.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("draft_folder must stay within the vault")
    if any(_UNSAFE_FILENAME.search(part) or part.endswith((" ", ".")) for part in parts):
        raise ValueError("draft_folder contains a non-portable path component")
    return parts


def _validate_title(title: str) -> str:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must not be empty")
    if title != title.strip() or title in {".", ".."}:
        raise ValueError("title must be a trimmed filename title")
    if _UNSAFE_FILENAME.search(title) or title.endswith((" ", ".")):
        raise ValueError("title contains characters that are unsafe in a filename")
    if title.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("title is a reserved filename")
    if len(title.encode("utf-8")) > 240:
        raise ValueError("title is too long for a portable filename")
    return title


def _render_draft(contents: str, source: SourceMetadata, timestamps: tuple[float, ...]) -> bytes:
    if not isinstance(contents, str):
        raise TypeError("contents must be a string")
    if not isinstance(timestamps, tuple):
        raise TypeError("timestamps must be a tuple of seconds")
    for timestamp in timestamps:
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise TypeError("timestamps must be numbers of seconds")
        if not isfinite(timestamp) or timestamp < 0:
            raise ValueError("timestamps must be finite and nonnegative")

    def single_line(value: str) -> str:
        return value.replace("\r", " ").replace("\n", " ")

    lines = ["## Source", "", f"- Source: {single_line(source.title or source.source_id)}"]
    lines.append(f"- URL: {single_line(source.original_url)}")
    if source.creator_name:
        lines.append(f"- Creator: {single_line(source.creator_name)}")
    if timestamps:
        values = ", ".join(f"{timestamp:g}s" for timestamp in timestamps)
        lines.append(f"- Relevant timestamps: {values}")
    separator = "" if not contents else "\n" if contents.endswith("\n") else "\n\n"
    return f"{contents}{separator}{'\n'.join(lines)}\n".encode()


def _open_directory_chain(root_fd: int, parts: tuple[str, ...]) -> int:
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            try:
                os.mkdir(part, dir_fd=current_fd)
            except FileExistsError:
                pass
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            next_fd = os.open(part, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _create_exclusive(directory_fd: int, title: str, contents: bytes) -> str:
    index = 1
    while True:
        suffix = "" if index == 1 else f" ({index})"
        filename = f"{title}{suffix}.md"
        try:
            file_fd = os.open(
                filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o666,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            index += 1
            continue
        try:
            with os.fdopen(file_fd, "wb") as draft_file:
                draft_file.write(contents)
        except BaseException:
            try:
                os.unlink(filename, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            raise
        return filename


def create_draft(
    settings: DraftWriterSettings,
    *,
    title: str,
    contents: str,
    source: SourceMetadata,
    timestamps: tuple[float, ...] = (),
) -> DraftResult:
    """Create a new draft beneath the configured folder, never modifying a note.

    Existing names receive a numeric suffix. Directory traversal uses file
    descriptors and rejects symlinks in the configured folder path.
    """
    title = _validate_title(title)
    parts = _draft_folder_parts(settings.draft_folder)
    payload = _render_draft(contents, source, timestamps)
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise RuntimeError("safe draft writing requires no-follow directory support")

    vault_path = Path(settings.vault_path)
    vault_info = vault_path.stat()
    if not stat.S_ISDIR(vault_info.st_mode):
        raise NotADirectoryError(f"vault path is not a directory: {vault_path}")
    resolved_vault = vault_path.resolve(strict=True)
    root_fd = os.open(resolved_vault, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory_fd = _open_directory_chain(root_fd, parts)
        try:
            filename = _create_exclusive(directory_fd, title, payload)
        finally:
            os.close(directory_fd)
    finally:
        os.close(root_fd)
    return DraftResult(resolved_vault.joinpath(*parts, filename))
