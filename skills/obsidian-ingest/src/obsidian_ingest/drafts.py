"""Publish curated Markdown as create-only vault drafts with generated provenance.

This is the filesystem safety boundary after acquisition and agent-led curation.
"""

import os
import re
import secrets
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from obsidian_ingest.extractor.metadata import SourceMetadata
from obsidian_ingest.extractor.providers import Citation

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
# Reject separators, control characters, and characters reserved by common filesystems.
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Markdown headings allow at most three leading spaces; indented code is not a heading.
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_SETEXT_HEADING = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_MAX_STEM_BYTES = 120  # Leave filename space for collision suffixes and the extension.
_RESERVED_HEADINGS = {"source", "sources", "source attribution", "provenance", "related notes"}
# Bidi embedding/override/isolate controls can visually reorder text; natural RTL text
# needs none of them, so stripping them does not damage Arabic or Hebrew.
_BIDI_CONTROLS = re.compile("[\u202a-\u202e\u2066-\u2069]")
_MAX_METADATA_CHARS = 300
_URL_ESCAPES = str.maketrans({char: quote(char, safe="") for char in "()<>[]| "})


def _inert(value: str, limit: int = _MAX_METADATA_CHARS) -> str:
    """Render untrusted source metadata as one line of literal text.

    Titles and creator names come from the source, so they must not inject wikilinks,
    links, tags, tables, code or HTML into the generated Source section.
    """
    text = _BIDI_CONTROLS.sub("", value)
    text = "".join(" " if unicodedata.category(char) == "Cc" else char for char in text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return re.sub(r"([\\`\[\]<>|#]|%(?=%)|(?<=%)%|=(?==)|(?<==)=)", r"\\\1", text)


@dataclass(frozen=True, slots=True)
class DraftWriterSettings:
    """Trusted destination configuration; the agent supplies no filesystem paths."""

    vault_path: Path
    draft_folder: str = "00 Inbox/AI Drafts"


@dataclass(frozen=True, slots=True)
class DraftResult:
    """Absolute path of the newly published draft."""

    path: Path


def _draft_folder_parts(folder: str) -> tuple[str, ...]:
    """Validate the trusted relative destination, raising ValueError for unsafe parts."""
    if not isinstance(folder, str) or not folder or "\\" in folder:
        raise ValueError("draft_folder must be a nonempty relative vault path")
    parts = tuple(folder.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("draft_folder must stay within the vault")
    if any(_UNSAFE_FILENAME.search(part) or part.endswith((" ", ".")) for part in parts):
        raise ValueError("draft_folder contains a non-portable path component")
    return parts


def _slugify(text: str) -> str:
    """Lowercase kebab-case stem: letters and digits kept, everything else a word break.

    The result contains no path separators, dots, or control characters.
    """
    text = unicodedata.normalize("NFC", text)
    stem = "-".join("".join(ch if ch.isalnum() else " " for ch in text).split()).lower()
    stem = stem.encode("utf-8")[:_MAX_STEM_BYTES].decode("utf-8", errors="ignore").rstrip("-")
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem += "-note"
    return stem


def _leading_h1(contents: str) -> str | None:
    """Return the text of an ATX H1 on the first non-blank line, if there is one."""
    for line in contents.splitlines():
        if not line.strip():
            continue
        heading = _ATX_HEADING.match(line)
        if heading and heading.group(1) == "#":
            return re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", heading.group(2) or "").strip()
        return None
    return None


def _draft_stem(filename: str | None, contents: str) -> str:
    """Derive a portable stem from an explicit name or leading H1; reject empty names."""
    if filename is not None:
        if not isinstance(filename, str):
            raise TypeError("filename must be a string")
        name = filename[:-3] if filename.lower().endswith(".md") else filename
    else:
        name = _leading_h1(contents) if isinstance(contents, str) else None
    stem = _slugify(name) if name is not None else ""
    if not stem:
        raise ValueError(
            "contents must start with an H1 title (# Title) or a filename must be given"
        )
    return stem


_PROVENANCE_HEADINGS = _RESERVED_HEADINGS - {"related notes"}


def _strip_trailing_source_section(contents: str) -> str:
    """Drop an agent-written Source section when it is the last section of the body.

    The writer appends verified provenance, so a final agent-written one is redundant.
    A Source section followed by further headings is still rejected by validation.
    """
    lines = contents.splitlines(keepends=True)
    in_fence = False
    start = None
    for index, line in enumerate(lines):
        stripped = line.lstrip(" ")
        if len(line) - len(stripped) <= 3 and stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        heading = None if in_fence else _ATX_HEADING.match(line.rstrip("\r\n"))
        if heading:
            text = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2) or "").strip().casefold()
            start = index if text in _PROVENANCE_HEADINGS else None
    if start is None:
        return contents
    body = "".join(lines[:start]).rstrip()
    while body.endswith(("---", "***")):
        body = body[:-3].rstrip()
    return body + "\n" if body else contents


def _render_draft(
    contents: str,
    source: SourceMetadata,
    citation: Citation | None = None,
    related_notes: tuple[tuple[str, str], ...] = (),
    leading_link: str | None = None,
    attribution: tuple[tuple[str, str], ...] | None = None,
) -> bytes:
    """Validate curated text and return UTF-8 Markdown with writer-owned attribution."""
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
        # Reasons are untrusted prose, not permission to inject Markdown structure.
        safe_reason = re.sub(r"([\\`*_{}\[\]()<>|])", r"\\\1", reason.strip())
        related_lines.append(f"- {link}: {safe_reason}")
    if citation is not None and not isinstance(citation, Citation):
        raise TypeError("citation must be a Citation")

    def single_line(value: str) -> str:
        return value.replace("\r", " ").replace("\n", " ")

    rendered_body = contents
    if related_lines:
        separator = "" if not rendered_body else "\n" if rendered_body.endswith("\n") else "\n\n"
        rendered_body = f"{rendered_body}{separator}## Related Notes\n\n" + "\n".join(related_lines)
    if leading_link is not None:
        rendered_body = f"{leading_link}\n\n{rendered_body}"

    lines = ["## Source", "", f"- Source: {_inert(source.title or source.source_id)}"]
    lines.append(f"- URL: {single_line(source.original_url).translate(_URL_ESCAPES)}")
    if attribution is None:
        attribution = (("Creator", source.creator_name),) if source.creator_name else ()
    for label, value in attribution:
        if not isinstance(label, str) or not label.isprintable() or not isinstance(value, str):
            raise TypeError("attribution must be (label, value) text pairs")
        lines.append(f"- {label}: {_inert(value)}")
    if citation is not None:
        # Provider-rendered from validated atoms, so its Markdown links are intended.
        value = _inert(citation.value, 1200) if citation.literal else single_line(citation.value)
        lines.append(f"- {single_line(citation.label)}: {value}")
    separator = "" if not rendered_body else "\n" if rendered_body.endswith("\n") else "\n\n"
    return f"{rendered_body}{separator}{'\n'.join(lines)}\n".encode()


def _validate_note_link_path(path: str) -> None:
    """Reject paths that could escape the vault or change a generated wikilink's meaning."""
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
    """Strip blockquote markers so the Related Notes check also sees quoted headings."""
    while marker := re.match(r"^ {0,3}>[ \t]?", line):
        line = line[marker.end() :]
    return line


def _validate_body(contents: str) -> None:
    """Allow at most one ATX H1 and reject writer-generated Source/Provenance/Related Notes headings."""
    lines = contents.splitlines()
    in_fence = False
    fence_marker = ""
    h1_count = 0
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
        # Heading-like text inside code is content, not document structure.
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
        if heading and heading.group(1) == "#":
            h1_count += 1
            if h1_count > 1:
                raise ValueError(
                    "contents have more than one H1 title; a draft holds one idea, so "
                    "call create_draft once per idea"
                )
        heading_text = heading.group(2) if heading else None
        if heading_text is not None:
            heading_text = re.sub(r"[ \t]+#+[ \t]*$", "", heading_text).strip()
            if heading_text.casefold() in _RESERVED_HEADINGS:
                if heading_text.casefold() == "related notes":
                    raise ValueError(
                        "Related Notes must be generated from verified note references"
                    )
                raise ValueError("contents must not include a Source or provenance section")
            continue
        if _SETEXT_HEADING.match(line) and index > 0 and lines[index - 1].strip():
            title = lines[index - 1].strip()
            if title.casefold() in _RESERVED_HEADINGS:
                if title.casefold() == "related notes":
                    raise ValueError(
                        "Related Notes must be generated from verified note references"
                    )
                raise ValueError("contents must not include a Source or provenance section")


# Descriptor-relative publication keeps destination traversal separate from agent text.


def _open_directory_chain(root_fd: int, parts: tuple[str, ...]) -> int:
    """Create/open destination components without following symlinks; return an owned fd."""
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
    """Return a private staging directory's name and owned descriptor beside the draft."""
    while True:
        name = f".obsidian-ingest-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=directory_fd)
        except FileExistsError:
            continue
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        return name, os.open(name, flags, dir_fd=directory_fd)


def _write_staged_file(staging_fd: int, contents: bytes) -> None:
    """Exclusively write and fsync the payload before it becomes a visible note."""
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


def _publish_staged_file(directory_fd: int, staging_fd: int, stem: str) -> str:
    """Atomically link the complete payload under a free name, never replacing a note."""
    index = 1
    while True:
        suffix = "" if index == 1 else f"-{index}"
        filename = f"{stem}{suffix}.md"
        try:
            # Hard-link creation fails on an existing name, unlike replacing rename.
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
    """Best-effort cleanup must not mask publication failures or remove the linked note."""
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
    """Fail closed with RuntimeError when POSIX create-only safety primitives are absent."""
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
    contents: str,
    source: SourceMetadata,
    filename: str | None = None,
    citation: Citation | None = None,
    related_notes: tuple[tuple[str, str], ...] = (),
    leading_link: str | None = None,
    attribution: tuple[tuple[str, str], ...] | None = None,
) -> DraftResult:
    """Create a new draft beneath the configured folder, never modifying a note.

    The filename is the kebab-case slug of `filename`, or of the contents' leading H1,
    which stays in the file. Existing names receive a `-2`, `-3`, ... suffix. Directory
    traversal uses file descriptors and rejects symlinks in the configured folder path.
    The configured directory tree must not be concurrently moved or modified by an
    untrusted process.
    """
    if isinstance(contents, str) and "\n" not in contents and "\\n" in contents:
        raise ValueError(
            "contents contain the characters \\n instead of line breaks; send real line "
            "breaks between the title and paragraphs"
        )
    stem = _draft_stem(filename, contents)
    if isinstance(contents, str):
        contents = _strip_trailing_source_section(contents)
    parts = _draft_folder_parts(settings.draft_folder)
    payload = _render_draft(contents, source, citation, related_notes, leading_link, attribution)
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
                published = _publish_staged_file(directory_fd, staging_fd, stem)
            finally:
                _cleanup_staging(directory_fd, staging_name, staging_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(root_fd)
    return DraftResult(resolved_vault.joinpath(*parts, published))
