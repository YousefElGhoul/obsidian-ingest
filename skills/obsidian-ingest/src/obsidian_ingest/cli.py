import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from obsidian_ingest import responses
from obsidian_ingest.config import default_config_path, load_vault_settings
from obsidian_ingest.extractor.metadata import fetch_metadata
from obsidian_ingest.extractor.transcript import print_transcript
from obsidian_ingest.responses import OperationError
from obsidian_ingest.source_cache import SourceCache

TOOL_COMMANDS = frozenset(
    {
        "ingest-source",
        "get-source-overview",
        "list-chapters",
        "list-exclusions",
        "read-transcript",
        "read-chapter",
        "get-thumbnail",
        "create-draft",
        "search-vault",
        "read-vault-note",
        "create-edit-suggestion",
    }
)


class _Parser(argparse.ArgumentParser):
    """Report invalid tool arguments with the same coded error shape as other failures."""

    def error(self, message: str) -> NoReturn:
        # Subparsers report their own errors; the top-level parser reports leftovers.
        command = self.prog.split()[-1]
        if command == "obsidian-ingest":
            command = next((value for value in sys.argv[1:] if not value.startswith("-")), "")
        if command in TOOL_COMMANDS:
            _emit_error(OperationError("INVALID_ARGUMENT", message))
            raise SystemExit(2)
        super().error(message)


def _run_mcp(
    vault_path: Path | None,
    draft_folder: str | None,
    cache_db: Path | None = None,
    *,
    config_path: Path | None = None,
) -> None:
    from obsidian_ingest.drafts import DraftWriterSettings
    from obsidian_ingest.mcp_server import create_server

    if config_path is not None or vault_path is None:
        selected_config = config_path or default_config_path()
        if vault_path is not None or draft_folder is not None:
            raise ValueError("--config cannot be combined with --vault or --draft-folder")
        if not selected_config.is_file():
            raise ValueError(
                f"Trusted config not found: {selected_config}; copy "
                "config/settings.example.toml to config/settings.toml and edit it"
            )
        vault_settings = load_vault_settings(selected_config)
        settings = DraftWriterSettings(vault_settings.root, vault_settings.draft_folder)
        server = create_server(settings, cache_db=cache_db, vault_settings=vault_settings)
    else:
        if not vault_path.is_dir():
            raise ValueError(f"Vault path must be an existing directory: {vault_path}")
        settings = DraftWriterSettings(vault_path, draft_folder or "00 Inbox/AI Drafts")
        server = create_server(settings, cache_db=cache_db)
    asyncio.run(server.run_stdio_async())


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, default=_json_default))


def _emit_error(error: OperationError) -> None:
    """Write one coded JSON error to stderr; the code meaning matches MCP tool errors."""
    print(
        json.dumps({"error": {"code": error.code, "message": error.message}}, ensure_ascii=False),
        file=sys.stderr,
    )


def _cache(args) -> SourceCache:
    return SourceCache(args.cache_db)


def _vault(path: Path | None):
    from obsidian_ingest.vault import VaultReader

    selected = path or default_config_path()
    if not selected.is_file():
        raise OperationError(
            "CONFIG_ERROR",
            f"Trusted config not found: {selected}; copy "
            "config/settings.example.toml to config/settings.toml and edit it",
        )
    try:
        return VaultReader(load_vault_settings(selected))
    except (TypeError, ValueError) as error:
        raise OperationError("CONFIG_ERROR", str(error)) from error


def _tool_parsers(subparsers) -> None:
    parser = subparsers.add_parser(
        "ingest-source", help="Ingest a URL and return a persistent source ID"
    )
    parser.add_argument("url")
    parser.add_argument("--include-sponsors", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser(
        "get-source-overview", help="Get source metadata and filtering status"
    )
    parser.add_argument("source_id")
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser("list-chapters", help="List creator chapters")
    parser.add_argument("source_id")
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser("list-exclusions", help="List excluded source ranges")
    parser.add_argument("source_id")
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser("read-transcript", help="Read a bounded transcript page")
    parser.add_argument("source_id")
    parser.add_argument("--start", type=float)
    parser.add_argument("--end", type=float)
    parser.add_argument("--include-excluded", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=responses.DEFAULT_PAGE_SIZE)
    parser.add_argument("--read-token", help="read_token from the previous page")
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser("read-chapter", help="Read a bounded creator-chapter page")
    parser.add_argument("source_id")
    parser.add_argument("chapter_index", type=int)
    parser.add_argument("--include-excluded", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=responses.DEFAULT_PAGE_SIZE)
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser(
        "get-thumbnail",
        help="Optional: save the video's thumbnail to a disposable cache and print its path",
    )
    parser.add_argument("source_id")
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser("create-draft", help="Create a source-attributed Markdown draft")
    parser.add_argument("source_id")
    parser.add_argument(
        "contents", help="Markdown starting with an H1 title (any characters); no source section"
    )
    parser.add_argument(
        "--filename", help="Optional filename; default is the kebab-case slug of the H1"
    )
    parser.add_argument("--timestamp", action="append", default=[], help="seconds or MM:SS")
    parser.add_argument(
        "--related-note",
        nargs=2,
        action="append",
        metavar=("PATH", "REASON"),
        help="Verified vault-relative note to link; repeatable; no revision is required",
    )
    parser.add_argument(
        "--read-token", required=True, help="read_token from the last transcript page"
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser(
        "search-vault",
        help="Search accessible notes; each whole word matches on its own, most matched words first",
    )
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--scope", choices=("title", "content", "both"), default="both")
    parser.add_argument("--cursor", help="next_cursor from an earlier search with the same query")
    parser.add_argument("--limit", type=int, default=responses.DEFAULT_SEARCH_LIMIT)
    parser.add_argument("--config", type=Path)

    parser = subparsers.add_parser(
        "read-vault-note", help="Read a bounded page by vault-relative path"
    )
    parser.add_argument("path")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=responses.DEFAULT_NOTE_READ_LIMIT)
    parser.add_argument("--revision", help="Required for continuation pages (offset > 0)")
    parser.add_argument("--config", type=Path)

    parser = subparsers.add_parser("create-edit-suggestion", help="Create a review-only edit draft")
    parser.add_argument("source_id")
    parser.add_argument("target_path")
    parser.add_argument("target_revision")
    parser.add_argument("change_summary")
    parser.add_argument("reasoning")
    parser.add_argument("proposed_content")
    parser.add_argument("--timestamp", action="append", default=[], help="seconds or MM:SS")
    parser.add_argument(
        "--read-token", required=True, help="read_token from the last transcript page"
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache-db", type=Path)


def _split(values: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in values.split(",") if item.strip())


def _setup(
    destination: Path,
    input_fn=input,
    *,
    vault_root: str | None = None,
    allowed: tuple[str, ...] = (),
    excluded: tuple[str, ...] | None = None,
    draft_folder: str | None = None,
) -> Path:
    """Create settings from explicit flags, prompting only when no vault root was given."""
    from obsidian_ingest.config import VaultSettings
    from obsidian_ingest.vault import VaultReader

    if destination.exists():
        raise ValueError(f"Refusing to overwrite existing settings: {destination}")
    interactive = vault_root is None
    if interactive:
        vault_root = input_fn("Absolute path to the existing Obsidian vault: ")
    root = Path(vault_root.strip()).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("Vault root must be an existing absolute directory")
    if interactive:
        allowed = _split(
            input_fn("Allowed vault paths (comma-separated; enter * only to allow all notes): ")
        )
        excluded = _split(input_fn("Excluded vault paths (comma-separated, or blank): "))
        draft_folder = input_fn("Draft folder [00 Inbox/AI Drafts]: ").strip()
    elif not allowed:
        raise ValueError("Non-interactive setup needs at least one --allow path")
    excluded = excluded or ()
    draft_folder = draft_folder or "00 Inbox/AI Drafts"
    settings = VaultSettings(root.resolve(strict=True), allowed, excluded, draft_folder)
    with VaultReader(settings):
        pass

    content = (
        "[vault]\n"
        f"root = {json.dumps(str(settings.root))}\n"
        f"allowed = {json.dumps(list(settings.allowed), ensure_ascii=False)}\n"
        f"excluded = {json.dumps(list(settings.excluded), ensure_ascii=False)}\n"
        f"draft_folder = {json.dumps(settings.draft_folder, ensure_ascii=False)}\n"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_fd = os.open(destination, flags, 0o600)
    except FileExistsError as error:
        raise ValueError(f"Refusing to overwrite existing settings: {destination}") from error
    with os.fdopen(file_fd, "w", encoding="utf-8") as config_file:
        config_file.write(content)
    return destination


def main() -> None:
    parser = _Parser(prog="obsidian-ingest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    transcript_parser = subparsers.add_parser("transcript", help="Print source captions")
    transcript_parser.add_argument("url", help="Video URL")

    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect normalized source data and best-effort sponsor filtering"
    )
    inspect_parser.add_argument("url", help="Video URL")

    metadata_parser = subparsers.add_parser("metadata", help="Print normalized source metadata")
    metadata_parser.add_argument("url", help="Video URL")

    mcp_parser = subparsers.add_parser("mcp", help="Run the local stdio MCP server")
    vault_source = mcp_parser.add_mutually_exclusive_group(required=False)
    vault_source.add_argument("--vault", type=Path, help="Existing vault; vault reads disabled")
    vault_source.add_argument("--config", type=Path, help="Trusted TOML vault configuration")
    mcp_parser.add_argument("--cache-db", type=Path)
    mcp_parser.add_argument("--draft-folder")

    setup_parser = subparsers.add_parser("setup", help="Create trusted skill-local vault settings")
    setup_parser.add_argument("--config", type=Path, default=default_config_path())
    setup_parser.add_argument(
        "--vault-root", help="Existing absolute vault path; with it, setup never prompts"
    )
    setup_parser.add_argument(
        "--allow", action="append", default=[], help="Allowed vault path (repeatable; * = all)"
    )
    setup_parser.add_argument(
        "--exclude", action="append", default=[], help="Excluded vault path (repeatable)"
    )
    setup_parser.add_argument("--draft-folder", help="Default: 00 Inbox/AI Drafts")

    _tool_parsers(subparsers)
    args = parser.parse_args()

    if args.command == "transcript":
        print_transcript(args.url)
    elif args.command == "inspect":
        from obsidian_ingest.inspection import format_source_inspection, inspect_source

        print(format_source_inspection(inspect_source(args.url)))
    elif args.command == "metadata":
        _emit(asdict(fetch_metadata(args.url)))
    elif args.command == "mcp":
        _run_mcp(args.vault, args.draft_folder, args.cache_db, config_path=args.config)
    elif args.command == "setup":
        destination = _setup(
            args.config,
            vault_root=args.vault_root,
            allowed=tuple(args.allow),
            excluded=tuple(args.exclude),
            draft_folder=args.draft_folder,
        )
        print(f"Saved trusted settings to {destination}")
    else:
        try:
            _emit(_run_tool_command(args))
        except Exception as error:
            mapped = responses.as_operation_error(error)
            if mapped is None:
                raise
            _emit_error(mapped)
            raise SystemExit(1) from None


def _run_tool_command(args) -> Any:
    """Run one tool command through the same application functions as MCP."""
    if args.command == "ingest-source":
        return responses.ingest(
            _cache(args), args.url, exclude_sponsors=not args.include_sponsors, refresh=args.refresh
        )

    if args.command in {"search-vault", "read-vault-note"}:
        with _vault(args.config) as vault:
            if args.command == "search-vault":
                return responses.search_vault(
                    vault,
                    args.query,
                    scope=args.scope,
                    cursor=args.cursor,
                    limit=args.limit,
                    identifier="path",
                )
            # Paths replace MCP handles; revision checks stay in VaultReader.read.
            reference = vault.get_path_reference(args.path, args.revision)
            return responses.read_vault_note(
                vault,
                reference.note_id,
                offset=args.offset,
                limit=args.limit,
                revision=args.revision,
                identifier="path",
            )

    source = responses.load_source(_cache(args), args.source_id)
    if args.command == "get-thumbnail":
        return _save_thumbnail(responses.thumbnail(source), source.metadata.source_id)
    if args.command == "get-source-overview":
        return responses.source_overview(source)
    if args.command == "list-chapters":
        return responses.chapters(source)
    if args.command == "list-exclusions":
        return responses.exclusions(source)
    if args.command == "read-transcript":
        return responses.transcript_page(
            source,
            start=args.start,
            end=args.end,
            include_excluded=args.include_excluded,
            offset=args.offset,
            limit=args.limit,
            read_token=args.read_token,
        )
    if args.command == "read-chapter":
        return responses.chapter_page(
            source,
            args.chapter_index,
            include_excluded=args.include_excluded,
            offset=args.offset,
            limit=args.limit,
        )
    return _run_write_command(args, source)


def _thumbnail_dir() -> Path:
    """A disposable host-local cache, never the vault."""
    cache = os.environ.get("XDG_CACHE_HOME")
    root = Path(cache) if cache and Path(cache).is_absolute() else Path.home() / ".cache"
    return root / "obsidian-ingest/thumbnails"


def _save_thumbnail(result: responses.Thumbnail, source_id: str) -> dict[str, object]:
    """The CLI cannot return image content, so it hands over a file path instead."""
    directory = _thumbnail_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{source_id}.{result.image.extension}"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(result.data)
    os.replace(temporary, path)
    return {"path": str(path)} | result.summary()


def _path_handle(vault, path: str, label: str) -> str:
    """Translate a CLI vault-relative path into the handle the shared operations accept."""
    from obsidian_ingest.vault import NoteUnavailable

    try:
        return vault.get_path_reference(path).note_id
    except NoteUnavailable as error:
        raise OperationError("NOTE_UNAVAILABLE", f"{label} is unavailable") from error


def _run_write_command(args, source) -> dict[str, str]:
    from obsidian_ingest.drafts import DraftWriterSettings

    with _vault(args.config) as vault:
        writer_settings = DraftWriterSettings(vault.settings.root, vault.settings.draft_folder)
        if args.command == "create-draft":
            # Ordinary related links need only current access, matching MCP note handles.
            related = [
                {"note_id": _path_handle(vault, path, "related note"), "reason": reason}
                for path, reason in args.related_note or ()
            ]
            return responses.write_draft(
                writer_settings,
                source,
                contents=args.contents,
                filename=args.filename,
                timestamps=tuple(args.timestamp),
                related_notes=related,
                vault=vault,
                read_token=args.read_token,
            )
        # The target revision is checked by the shared edit-suggestion operation.
        return responses.write_edit_suggestion(
            writer_settings,
            source,
            vault,
            target_note_id=_path_handle(vault, args.target_path, "target note"),
            target_revision=args.target_revision,
            change_summary=args.change_summary,
            reasoning=args.reasoning,
            proposed_content=args.proposed_content,
            timestamps=tuple(args.timestamp),
            read_token=args.read_token,
        )


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, Path)):
        return str(value) if isinstance(value, Path) else value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
