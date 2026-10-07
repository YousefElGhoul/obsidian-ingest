"""Safe creation of provenance-preserving Markdown drafts."""

import os
import re
import secrets
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


def _create_staging_directory(directory_fd: int) -> tuple[str, int]:
    while True:
        name = f".obsidian-ingest-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=directory_fd)
        except FileExistsError:
            continue
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        return name, os.open(name, flags, dir_fd=directory_fd)


def _write_staged_file(staging_fd: int, contents: bytes) -> None:
    file_fd = os.open(
        ".draft.tmp",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=staging_fd,
    )
    try:
        with os.fdopen(file_fd, "wb") as staged_file:
            view = memoryview(contents)
            while view:
                written = staged_file.write(view)
                if written == 0:
                    raise OSError("failed to write staged draft")
                view = view[written:]
            staged_file.flush()
            os.fsync(staged_file.fileno())
    except BaseException:
        try:
            os.unlink(".draft.tmp", dir_fd=staging_fd)
        except OSError:
            pass
        raise


def _publish_staged_file(directory_fd: int, staging_fd: int, title: str) -> str:
    index = 1
    while True:
        suffix = "" if index == 1 else f" ({index})"
        filename = f"{title}{suffix}.md"
        try:
            os.link(
                ".draft.tmp",
                filename,
                src_dir_fd=staging_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            index += 1
            continue
        except OSError as error:
            raise OSError(
                f"could not atomically publish draft with a hard link: {error}"
            ) from error
        return filename


def _cleanup_staging(directory_fd: int, staging_name: str, staging_fd: int) -> None:
    try:
        try:
            os.unlink(".draft.tmp", dir_fd=staging_fd)
        except FileNotFoundError:
            pass
    except OSError:
        pass
    finally:
        try:
            os.close(staging_fd)
        except OSError:
            pass
    try:
        os.rmdir(staging_name, dir_fd=directory_fd)
    except OSError:
        pass


def _require_writer_capabilities() -> None:
    required_flags = ("O_NOFOLLOW", "O_DIRECTORY")
    missing_flags = tuple(name for name in required_flags if not hasattr(os, name))
    required_dir_fd = (os.open, os.mkdir, os.unlink, os.rmdir, os.link)
    supported_dir_fd = getattr(os, "supports_dir_fd", ())
    missing_operations = tuple(
        operation.__name__ for operation in required_dir_fd if operation not in supported_dir_fd
    )
    if os.link not in getattr(os, "supports_follow_symlinks", ()):
        missing_operations += ("link(follow_symlinks=False)",)
    if not hasattr(os, "fsync"):
        missing_operations += ("fsync",)
    if missing_flags or missing_operations:
        missing = ", ".join((*missing_flags, *missing_operations))
        raise RuntimeError(
            f"safe draft writing requires POSIX filesystem support; unavailable: {missing}"
        )


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
    descriptors and rejects symlinks in the configured folder path. The configured
    directory tree must not be concurrently moved or modified by an untrusted process.
    """
    title = _validate_title(title)
    parts = _draft_folder_parts(settings.draft_folder)
    payload = _render_draft(contents, source, timestamps)
    _require_writer_capabilities()

    vault_path = Path(settings.vault_path)
    vault_info = vault_path.stat()
    if not stat.S_ISDIR(vault_info.st_mode):
        raise NotADirectoryError(f"vault path is not a directory: {vault_path}")
    resolved_vault = vault_path.resolve(strict=True)
    root_fd = os.open(resolved_vault, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory_fd = _open_directory_chain(root_fd, parts)
        try:
            staging_name, staging_fd = _create_staging_directory(directory_fd)
            try:
                _write_staged_file(staging_fd, payload)
                filename = _publish_staged_file(directory_fd, staging_fd, title)
            finally:
                _cleanup_staging(directory_fd, staging_name, staging_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(root_fd)
    return DraftResult(resolved_vault.joinpath(*parts, filename))
