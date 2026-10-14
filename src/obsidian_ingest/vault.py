"""Restricted, read-only vault search and note access."""

from __future__ import annotations

import base64
import codecs
import hashlib
import hmac
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from obsidian_ingest.config import VaultSettings

MAX_SEARCH_LIMIT = 20
MAX_QUERY_LENGTH = 256
MAX_SNIPPET_CHARS = 240
MAX_NOTE_READ_CHARS = 8000
MAX_NOTE_RESPONSE_BYTES = 16 * 1024
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class NoteUnavailable(RuntimeError):
    """A note reference is missing, inaccessible, excluded, or no longer valid."""


class NoteChanged(RuntimeError):
    """The selected note changed since the previous operation."""


@dataclass(frozen=True, slots=True)
class NoteReference:
    note_id: str
    path: str
    revision: str
    title: str
    linkable: bool


class VaultReader:
    """Apply one trusted path policy to recursive search and every note operation."""

    def __init__(self, settings: VaultSettings) -> None:
        if not settings.root.is_absolute() or not settings.root.is_dir():
            raise ValueError("Vault root must be an existing absolute directory")
        if not isinstance(settings.allowed, tuple) or not settings.allowed:
            raise ValueError("At least one allowed vault path is required")
        if not isinstance(settings.excluded, tuple):
            raise TypeError("Excluded vault paths must be a tuple")
        if "*" in settings.allowed and settings.allowed != ("*",):
            raise ValueError("'*' must be the only allowed vault path")
        for label, paths in (("allowed", settings.allowed), ("excluded", settings.excluded)):
            for value in paths:
                if (
                    not isinstance(value, str)
                    or not value
                    or value.startswith("/")
                    or "\\" in value
                    or any(ord(character) < 32 for character in value)
                    or any(part in {"", ".", ".."} for part in value.split("/"))
                ):
                    raise ValueError(f"Unsafe {label} vault path")
        if len(set(settings.allowed)) != len(settings.allowed) or len(
            set(settings.excluded)
        ) != len(settings.excluded):
            raise ValueError("Allowed and excluded paths must not contain duplicates")
        if (
            not isinstance(settings.draft_folder, str)
            or not settings.draft_folder
            or settings.draft_folder.startswith("/")
            or "\\" in settings.draft_folder
            or any(ord(character) < 32 for character in settings.draft_folder)
            or any(part in {"", ".", ".."} for part in settings.draft_folder.split("/"))
        ):
            raise ValueError("Unsafe draft destination")
        self.settings = settings
        self.root = settings.root.resolve(strict=True)
        self._root_fd = os.open(self.root, _DIRECTORY_FLAGS)
        self._secret = secrets.token_bytes(32)
        self._references: dict[str, NoteReference] = {}
        try:
            self._validate_settings()
        except BaseException:
            os.close(self._root_fd)
            raise

    def _excluded(self, relative: str) -> bool:
        return any(
            relative == item or relative.startswith(item + "/") for item in self.settings.excluded
        )

    def _allowed(self, relative: str) -> bool:
        if self._excluded(relative):
            return False
        return self.settings.allowed == ("*",) or any(
            relative == item or relative.startswith(item + "/") for item in self.settings.allowed
        )

    def _could_contain_allowed(self, relative: str) -> bool:
        if self._excluded(relative):
            return False
        if self.settings.allowed == ("*",):
            return True
        return any(
            allowed == relative
            or allowed.startswith(relative + "/")
            or relative.startswith(allowed + "/")
            for allowed in self.settings.allowed
        )

    def _open_relative(self, relative: str, *, directory: bool = False) -> int:
        if not relative or relative.startswith("/") or "\\" in relative:
            raise NoteUnavailable
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise NoteUnavailable
        if not self._allowed(relative):
            raise NoteUnavailable
        parent = os.dup(self._root_fd)
        fd: int | None = None
        try:
            for component in parts[:-1]:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent)
                os.close(parent)
                parent = child
            before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            valid_before = (
                stat.S_ISDIR(before.st_mode)
                if directory
                else stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            )
            if not valid_before:
                raise NoteUnavailable
            flags = _DIRECTORY_FLAGS if directory else os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            fd = os.open(parts[-1], flags, dir_fd=parent)
            info = os.fstat(fd)
            valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
            if (
                not valid_type
                or (not directory and info.st_nlink != 1)
                or (before.st_dev, before.st_ino) != (info.st_dev, info.st_ino)
            ):
                os.close(fd)
                fd = None
                raise NoteUnavailable
            assert fd is not None
            return fd
        except (OSError, ValueError) as error:
            if fd is not None:
                os.close(fd)
            raise NoteUnavailable from error
        finally:
            os.close(parent)

    def _validate_settings(self) -> None:
        destination = self.settings.draft_folder
        for allowed in self.settings.allowed:
            if allowed == "*":
                continue
            try:
                fd = self._open_relative(allowed)
            except NoteUnavailable as error:
                try:
                    fd = self._open_relative(allowed, directory=True)
                except NoteUnavailable as directory_error:
                    missing = isinstance(directory_error.__cause__, FileNotFoundError)
                    destination_parent = destination == allowed or destination.startswith(
                        allowed + "/"
                    )
                    if missing and destination_parent and not self._excluded(allowed):
                        continue
                    raise ValueError(f"Allowed path is missing or unsafe: {allowed}") from error
            else:
                info = os.fstat(fd)
                if not (stat.S_ISREG(info.st_mode) and allowed.endswith(".md")):
                    os.close(fd)
                    try:
                        fd = self._open_relative(allowed, directory=True)
                    except NoteUnavailable as error:
                        raise ValueError(
                            f"Allowed path is not a note or directory: {allowed}"
                        ) from error
            os.close(fd)
        if self._excluded(destination) or not self._allowed(destination):
            raise ValueError(
                "Draft destination must be within allowed paths and outside exclusions"
            )
        parent = os.dup(self._root_fd)
        try:
            for component in destination.split("/"):
                try:
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent)
                except FileNotFoundError:
                    break
                except OSError as error:
                    raise ValueError(
                        "Draft destination contains an unsafe or non-directory component"
                    ) from error
                os.close(parent)
                parent = child
            if not os.access(".", os.W_OK | os.X_OK, dir_fd=parent):
                raise ValueError("Draft destination is not writable by the server process")
        finally:
            os.close(parent)

    def _paths(self):
        def walk(parent_fd: int, prefix: str):
            try:
                names = sorted(os.listdir(parent_fd))
            except OSError:
                return
            for name in names:
                relative = f"{prefix}/{name}" if prefix else name
                if self._excluded(relative):
                    continue
                try:
                    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if not self._could_contain_allowed(relative):
                        continue
                    try:
                        child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
                    except OSError:
                        continue
                    try:
                        yield from walk(child_fd, relative)
                    finally:
                        os.close(child_fd)
                elif (
                    name.endswith(".md") and stat.S_ISREG(info.st_mode) and self._allowed(relative)
                ):
                    yield relative

        yield from walk(self._root_fd, "")

    def _scan_file(
        self, relative: str, terms: tuple[str, ...] = (), *, search_content: bool = False
    ) -> tuple[str, bool, str]:
        fd = self._open_relative(relative)
        digest = hashlib.sha256()
        decoder = codecs.getincrementaldecoder("utf-8")()
        carry = ""
        found_terms: set[str] = set()
        snippet = ""
        carry_length = max(MAX_SNIPPET_CHARS, max((len(term) for term in terms), default=0))
        try:
            with os.fdopen(fd, "rb") as file:
                while block := file.read(64 * 1024):
                    digest.update(block)
                    decoded = decoder.decode(block)
                    if search_content:
                        combined = carry + decoded
                        folded = combined.casefold()
                        for term in terms:
                            if term not in found_terms and term in folded:
                                found_terms.add(term)
                                if not snippet:
                                    snippet = self._snippet(combined, (term,))
                        carry = combined[-carry_length:]
                decoded = decoder.decode(b"", final=True)
                if search_content:
                    combined = carry + decoded
                    folded = combined.casefold()
                    for term in terms:
                        if term not in found_terms and term in folded:
                            found_terms.add(term)
                            if not snippet:
                                snippet = self._snippet(combined, (term,))
        except (OSError, UnicodeDecodeError) as error:
            raise NoteUnavailable from error
        return "sha256:" + digest.hexdigest(), all(term in found_terms for term in terms), snippet

    def _register(self, relative: str, revision: str) -> NoteReference:
        note_id = "note_" + secrets.token_urlsafe(18)
        linkable = (
            not any(character in relative for character in "#^|[]\\:")
            and "%%" not in relative
            and not any(ord(character) < 32 for character in relative)
        )
        reference = NoteReference(note_id, relative, revision, Path(relative).stem, linkable)
        self._references[note_id] = reference
        return reference

    def _cursor(self, query: str, scope: str, offset: int) -> str:
        payload = json.dumps([query, scope, offset], separators=(",", ":")).encode()
        signed = (
            len(payload).to_bytes(2, "big") + payload + hmac.digest(self._secret, payload, "sha256")
        )
        return base64.urlsafe_b64encode(signed).decode().rstrip("=")

    def _decode_cursor(self, cursor: str, query: str, scope: str) -> int:
        try:
            if not isinstance(cursor, str) or len(cursor) > 8192:
                raise ValueError
            encoded = cursor.encode()
            raw = base64.b64decode(
                encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
            payload_size = int.from_bytes(raw[:2], "big")
            payload = raw[2 : 2 + payload_size]
            signature = raw[2 + payload_size :]
            expected = hmac.digest(self._secret, payload, "sha256")
            cursor_query, cursor_scope, offset = json.loads(payload)
            if (
                len(raw) != payload_size + 34
                or not hmac.compare_digest(signature, expected)
                or (cursor_query, cursor_scope) != (query, scope)
            ):
                raise ValueError
            if type(offset) is not int or offset < 0:
                raise ValueError
            return offset
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("INVALID_CURSOR") from error

    def search(
        self, query: str = "", *, scope: str = "both", cursor: str | None = None, limit: int = 10
    ) -> dict:
        if not isinstance(query, str) or len(query) > MAX_QUERY_LENGTH:
            raise ValueError("INVALID_ARGUMENT: query must be at most 256 characters")
        if scope not in {"title", "content", "both"}:
            raise ValueError("INVALID_ARGUMENT: scope must be title, content, or both")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_SEARCH_LIMIT
        ):
            raise ValueError(f"INVALID_ARGUMENT: limit must be between 1 and {MAX_SEARCH_LIMIT}")
        offset = self._decode_cursor(cursor, query, scope) if cursor is not None else 0
        terms = tuple(term.casefold() for term in query.split())
        page = []
        matched = 0
        for relative in self._paths():
            try:
                revision, content_matches, snippet = self._scan_file(
                    relative, terms, search_content=scope != "title" and bool(terms)
                )
            except NoteUnavailable:
                continue
            title = Path(relative).stem
            title_matches = all(term in title.casefold() for term in terms)
            found = (
                not terms
                or (scope == "title" and title_matches)
                or (scope == "content" and content_matches)
                or (scope == "both" and (title_matches or content_matches))
            )
            if not found:
                continue
            if matched >= offset and len(page) < limit:
                reference = self._register(relative, revision)
                page.append(
                    {
                        "note_id": reference.note_id,
                        "path": relative,
                        "title": title,
                        "revision": reference.revision,
                        "snippet": snippet if content_matches else "",
                        "linkable": reference.linkable,
                    }
                )
            matched += 1
        next_offset = offset + len(page)
        has_more = matched > next_offset
        had_page = bool(page)
        while page:
            result = {
                "notes": page,
                "limit": limit,
                "next_cursor": self._cursor(query, scope, offset + len(page))
                if matched > offset + len(page)
                else None,
            }
            text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            envelope = json.dumps(
                {"content": [{"type": "text", "text": text}]},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            if len(envelope) <= MAX_NOTE_RESPONSE_BYTES - 1024:
                return result
            page.pop()
        if had_page:
            raise ValueError("INVALID_ARGUMENT: a search result cannot fit in a bounded response")
        if has_more:
            raise ValueError("INVALID_ARGUMENT: search result cannot fit in a bounded response")
        return {"notes": [], "limit": limit, "next_cursor": None}

    @staticmethod
    def _snippet(content: str, terms: tuple[str, ...]) -> str:
        if not terms:
            return ""
        folded = content.casefold()
        position = min((found for term in terms if (found := folded.find(term)) >= 0), default=0)
        start = max(0, position - MAX_SNIPPET_CHARS // 3)
        return content[start : start + MAX_SNIPPET_CHARS].replace("\n", " ")

    def get_reference(self, note_id: str, revision: str | None = None) -> NoteReference:
        if not isinstance(note_id, str) or len(note_id) > 80:
            raise NoteUnavailable
        reference = self._references.get(note_id)
        if reference is None:
            raise NoteUnavailable
        if revision is not None and revision != reference.revision:
            raise NoteChanged
        try:
            actual_revision, _, _ = self._scan_file(reference.path)
        except NoteUnavailable as error:
            raise NoteUnavailable from error
        if revision is not None and revision != actual_revision:
            raise NoteChanged
        if actual_revision != reference.revision:
            raise NoteChanged
        return reference

    def get_linkable_reference(self, note_id: str) -> NoteReference:
        """Revalidate access and existence without pinning ordinary links to a revision."""
        if not isinstance(note_id, str) or len(note_id) > 80:
            raise NoteUnavailable
        reference = self._references.get(note_id)
        if reference is None:
            raise NoteUnavailable
        fd = self._open_relative(reference.path)
        os.close(fd)
        return reference

    def _read_range(self, relative: str, offset: int, selected_limit: int) -> tuple[str, int, str]:
        fd = self._open_relative(relative)
        digest = hashlib.sha256()
        decoder = codecs.getincrementaldecoder("utf-8")()
        total_characters = 0
        try:
            with os.fdopen(fd, "rb") as file:
                while block := file.read(64 * 1024):
                    digest.update(block)
                    total_characters += len(decoder.decode(block))
                total_characters += len(decoder.decode(b"", final=True))
                first_revision = "sha256:" + digest.hexdigest()
                file.seek(0)
                digest = hashlib.sha256()
                decoder = codecs.getincrementaldecoder("utf-8")()
                chars_to_skip = offset
                chars_remaining = selected_limit
                selected_parts = []
                while block := file.read(64 * 1024):
                    digest.update(block)
                    text = decoder.decode(block)
                    if chars_to_skip:
                        skipped = min(chars_to_skip, len(text))
                        text = text[skipped:]
                        chars_to_skip -= skipped
                    if chars_remaining and text:
                        part = text[:chars_remaining]
                        selected_parts.append(part)
                        chars_remaining -= len(part)
                decoder.decode(b"", final=True)
                second_revision = "sha256:" + digest.hexdigest()
        except (OSError, UnicodeDecodeError) as error:
            raise NoteUnavailable from error
        if first_revision != second_revision:
            raise NoteChanged
        return "".join(selected_parts), total_characters, first_revision

    def read(
        self, note_id: str, *, offset: int = 0, limit: int = 4000, revision: str | None = None
    ) -> dict:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("INVALID_ARGUMENT: offset must be nonnegative")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_NOTE_READ_CHARS
        ):
            raise ValueError(f"INVALID_ARGUMENT: limit must be between 1 and {MAX_NOTE_READ_CHARS}")
        if offset and revision is None:
            raise ValueError("INVALID_ARGUMENT: revision is required for continuation pages")
        reference = self._references.get(note_id)
        if reference is None:
            raise NoteUnavailable
        if revision is not None and revision != reference.revision:
            raise NoteChanged
        selected_limit = min(limit, MAX_NOTE_READ_CHARS)
        selected, total_characters, actual_revision = self._read_range(
            reference.path, offset, selected_limit
        )
        if actual_revision != reference.revision or (
            revision is not None and revision != actual_revision
        ):
            raise NoteChanged
        page = None
        while True:
            next_offset = offset + len(selected)
            candidate = {
                "note_id": note_id,
                "path": reference.path,
                "title": reference.title,
                "revision": reference.revision,
                "text": selected,
                "offset": offset,
                "limit": limit,
                "total_characters": total_characters,
                "next_offset": next_offset if next_offset < total_characters else None,
            }
            envelope = json.dumps(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                candidate, ensure_ascii=False, separators=(",", ":")
                            ),
                        }
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
            if len(envelope) <= MAX_NOTE_RESPONSE_BYTES - 1024:
                page = candidate
                break
            selected_limit //= 2
            if selected_limit == 0:
                break
            selected = selected[:selected_limit]
        if page is None:
            raise ValueError("INVALID_ARGUMENT: note content cannot fit in a bounded response")
        return page

    def close(self) -> None:
        os.close(self._root_fd)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
