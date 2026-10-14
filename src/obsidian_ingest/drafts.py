"""Safe creation of provenance-preserving Markdown drafts."""

import os
import re
import secrets
import stat
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from urllib.parse import urlencode

from obsidian_ingest.extractor import Provider
from obsidian_ingest.extractor.metadata import SourceMetadata
from obsidian_ingest.extractor.transcript import format_timestamp

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_SETEXT_HEADING = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_RESERVED_HEADINGS = {"source", "sources", "source attribution", "provenance", "related notes"}


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


def _render_draft(
    contents: str,
    source: SourceMetadata,
    timestamps: tuple[float, ...],
    related_notes: tuple[tuple[str, str], ...] = (),
    leading_link: str | None = None,
) -> bytes:
    if not isinstance(contents, str):
        raise TypeError("contents must be a string")
    if leading_link is None:
        _validate_body(contents)
    else:
        if not leading_link.startswith("[[") or not leading_link.endswith("]]"):
            raise ValueError("leading_link must be one verified wikilink")
        _validate_note_link_path(leading_link[2:-2])
        _validate_body(f"{leading_link}\n\n{contents}")
    if len(related_notes) > 5:
        raise ValueError("related_notes must contain at most five notes")
    related_lines = []
    seen_links = set()
    for link, reason in related_notes:
        path = (
            link[2:-2]
            if isinstance(link, str) and link.startswith("[[") and link.endswith("]]")
            else ""
        )
        _validate_note_link_path(path)
        if link in seen_links:
            continue
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 300
            or "\n" in reason
            or "\r" in reason
        ):
            raise ValueError(
                "related note reason must be a nonempty single line of at most 300 characters"
            )
        if "[[" in reason or "]]" in reason:
            raise ValueError("related note reason must not contain wikilinks")
        seen_links.add(link)
        safe_reason = re.sub(r"([\\`*_{}\[\]()<>|])", r"\\\1", reason.strip())
        related_lines.append(f"- {link}: {safe_reason}")
    if not isinstance(timestamps, tuple):
        raise TypeError("timestamps must be a tuple of seconds")
    for timestamp in timestamps:
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise TypeError("timestamps must be numbers of seconds")
        if not isfinite(timestamp) or timestamp < 0:
            raise ValueError("timestamps must be finite and nonnegative")

    def single_line(value: str) -> str:
        return value.replace("\r", " ").replace("\n", " ")

    rendered_body = contents
    if related_lines:
        separator = "" if not rendered_body else "\n" if rendered_body.endswith("\n") else "\n\n"
        rendered_body = f"{rendered_body}{separator}## Related Notes\n\n" + "\n".join(related_lines)
    if leading_link is not None:
        rendered_body = f"{leading_link}\n\n{rendered_body}"

    lines = ["## Source", "", f"- Source: {single_line(source.title or source.source_id)}"]
    lines.append(f"- URL: {single_line(source.original_url)}")
    if source.creator_name:
        lines.append(f"- Creator: {single_line(source.creator_name)}")
    if timestamps:
        formatted = []
        for timestamp in timestamps:
            if source.provider == Provider.YOUTUBE:
                seconds = int(timestamp)
                url = "https://www.youtube.com/watch?" + urlencode(
                    {"v": source.source_id, "t": f"{seconds}s"}
                )
                formatted.append(f"[{format_timestamp(seconds)}]({url})")
            else:
                formatted.append(f"{timestamp:g}s")
        values = ", ".join(formatted)
        lines.append(f"- Relevant timestamps: {values}")
    separator = "" if not rendered_body else "\n" if rendered_body.endswith("\n") else "\n\n"
    return f"{rendered_body}{separator}{'\n'.join(lines)}\n".encode()


def _validate_note_link_path(path: str) -> None:
    parts = path.split("/")
    if (
        not path.endswith(".md")
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in parts)
        or any(character in path for character in "#^|[]\\:")
        or "%%" in path
        or any(ord(character) < 32 for character in path)
    ):
        raise ValueError("wikilink path is not safe")


def _unquote_heading(line: str) -> str:
    while marker := re.match(r"^ {0,3}>[ \t]?", line):
        line = line[marker.end() :]
    return line


def _validate_body(contents: str) -> None:
    lines = contents.splitlines()
    in_fence = False
    fence_marker = ""
    first_heading_checked = False
    for index, line in enumerate(lines):
        stripped = line.lstrip(" ")
        if len(line) - len(stripped) <= 3 and stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
            continue
        if in_fence or line.startswith(("    ", "\t")):
            continue
        unquoted = _unquote_heading(line)
        quoted_heading = _ATX_HEADING.match(unquoted)
        if quoted_heading:
            quoted_title = re.sub(r"[ \t]+#+[ \t]*$", "", quoted_heading.group(2) or "").strip()
            if quoted_title.casefold() == "related notes":
                raise ValueError("Related Notes must be generated from verified note references")
        if index and _SETEXT_HEADING.match(unquoted):
            previous_title = _unquote_heading(lines[index - 1]).strip()
            if previous_title.casefold() == "related notes":
                raise ValueError("Related Notes must be generated from verified note references")
        heading = _ATX_HEADING.match(line)
        heading_text = heading.group(2) if heading else None
        if heading_text is not None:
            heading_text = re.sub(r"[ \t]+#+[ \t]*$", "", heading_text).strip()
            if heading_text.casefold() in _RESERVED_HEADINGS:
                if heading_text.casefold() == "related notes":
                    raise ValueError(
                        "Related Notes must be generated from verified note references"
                    )
                raise ValueError("contents must not include a Source or provenance section")
            if not first_heading_checked and heading.group(1) in ("#", "##"):
                preceding = lines[:index]
                if all(not item.strip() for item in preceding):
                    raise ValueError(
                        "contents must be body-only and must not start with an H1 title"
                    )
            first_heading_checked = True
            continue
        setext = _SETEXT_HEADING.match(line)
        if setext and index > 0 and lines[index - 1].strip():
            title = lines[index - 1].strip()
            if title.casefold() in _RESERVED_HEADINGS:
                if title.casefold() == "related notes":
                    raise ValueError(
                        "Related Notes must be generated from verified note references"
                    )
                raise ValueError("contents must not include a Source or provenance section")
            if setext.group(1).startswith("=") and all(
                not item.strip() for item in lines[: index - 1]
            ):
                raise ValueError("contents must be body-only and must not start with an H1 title")
            first_heading_checked = True
        elif line.strip() and not first_heading_checked:
            first_heading_checked = True


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
    related_notes: tuple[tuple[str, str], ...] = (),
    leading_link: str | None = None,
) -> DraftResult:
    """Create a new draft beneath the configured folder, never modifying a note.

    Existing names receive a numeric suffix. Directory traversal uses file
    descriptors and rejects symlinks in the configured folder path. The configured
    directory tree must not be concurrently moved or modified by an untrusted process.
    """
    title = _validate_title(title)
    parts = _draft_folder_parts(settings.draft_folder)
    payload = _render_draft(contents, source, timestamps, related_notes, leading_link)
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
