import argparse
import asyncio
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from obsidian_ingest.config import default_config_path, load_vault_settings
from obsidian_ingest.extractor.metadata import fetch_metadata
from obsidian_ingest.extractor.transcript import print_transcript
from obsidian_ingest.source import (
    list_chapters,
    read_chapter_entries,
    read_transcript_entries,
)
from obsidian_ingest.source_cache import SourceCache


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


def _cache(args) -> SourceCache:
    return SourceCache(args.cache_db)


def _vault(path: Path | None):
    selected = path or default_config_path()
    if not selected.is_file():
        raise ValueError(
            f"Trusted config not found: {selected}; copy "
            "config/settings.example.toml to config/settings.toml and edit it"
        )
    settings = load_vault_settings(selected)
    from obsidian_ingest.vault import VaultReader

    return VaultReader(settings)


def _source_overview(source) -> dict:
    from obsidian_ingest.mcp_server import _source_overview as serialize_overview

    return serialize_overview(source)


def _segment_page(entries, offset: int, limit: int) -> dict:
    from obsidian_ingest.mcp_server import _segment_page as serialize_page

    return json.loads(serialize_page(entries, offset, limit))


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
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser("read-chapter", help="Read a bounded creator-chapter page")
    parser.add_argument("source_id")
    parser.add_argument("chapter_index", type=int)
    parser.add_argument("--include-excluded", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser("create-draft", help="Create a source-attributed Markdown draft")
    parser.add_argument("source_id")
    parser.add_argument("title")
    parser.add_argument("contents", help="Markdown body only; no H1 or source section")
    parser.add_argument("--timestamp", type=float, action="append", default=[])
    parser.add_argument(
        "--related-note", nargs=3, action="append", metavar=("PATH", "REVISION", "REASON")
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache-db", type=Path)

    parser = subparsers.add_parser("search-vault", help="Search accessible Markdown notes")
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--scope", choices=("title", "content", "both"), default="both")
    parser.add_argument("--cursor")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--config", type=Path)

    parser = subparsers.add_parser(
        "read-vault-note", help="Read a bounded page by vault-relative path"
    )
    parser.add_argument("path")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=4000)
    parser.add_argument("--revision")
    parser.add_argument("--config", type=Path)

    parser = subparsers.add_parser("create-edit-suggestion", help="Create a review-only edit draft")
    parser.add_argument("source_id")
    parser.add_argument("target_path")
    parser.add_argument("target_revision")
    parser.add_argument("change_summary")
    parser.add_argument("reasoning")
    parser.add_argument("proposed_content")
    parser.add_argument("--timestamp", type=float, action="append", default=[])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--cache-db", type=Path)


def _setup(destination: Path, input_fn=input) -> Path:
    from obsidian_ingest.config import VaultSettings
    from obsidian_ingest.vault import VaultReader

    if destination.exists():
        raise ValueError(f"Refusing to overwrite existing settings: {destination}")
    root_value = input_fn("Absolute path to the existing Obsidian vault: ").strip()
    root = Path(root_value).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("Vault root must be an existing absolute directory")
    allowed_values = input_fn(
        "Allowed vault paths (comma-separated; enter * only to allow all notes): "
    )
    allowed = tuple(item.strip() for item in allowed_values.split(",") if item.strip())
    excluded_values = input_fn("Excluded vault paths (comma-separated, or blank): ")
    excluded = tuple(item.strip() for item in excluded_values.split(",") if item.strip())
    draft_folder = input_fn("Draft folder [00 Inbox/AI Drafts]: ").strip() or "00 Inbox/AI Drafts"
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
    parser = argparse.ArgumentParser(prog="obsidian-ingest")
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
        destination = _setup(args.config)
        print(f"Saved trusted settings to {destination}")
    else:
        _run_tool_command(args)


def _run_tool_command(args) -> None:
    from obsidian_ingest.curation import create_edit_suggestion, verified_related_notes
    from obsidian_ingest.drafts import DraftWriterSettings, create_draft

    if args.command == "ingest-source":
        result = _cache(args).ingest(
            args.url, exclude_sponsors=not args.include_sponsors, refresh=args.refresh
        )
        _emit(
            {
                "source_id": result.source_id,
                "cached": result.cached,
                "overview": _source_overview(result.source),
            }
        )
        return

    if args.command in {
        "get-source-overview",
        "list-chapters",
        "list-exclusions",
        "read-transcript",
        "read-chapter",
    }:
        source = _cache(args).get(args.source_id)
        if args.command == "get-source-overview":
            _emit(_source_overview(source))
        elif args.command == "list-chapters":
            _emit(
                [
                    {"index": index, "title": item.title, "start": item.start, "end": item.end}
                    for index, item in enumerate(list_chapters(source))
                ]
            )
        elif args.command == "list-exclusions":
            _emit(
                [
                    {
                        "range_index": range_index,
                        "start": exclusion.start,
                        "end": exclusion.end,
                        "reason": exclusion.reason,
                        "provenance": exclusion.provenance,
                        "source_indices": [
                            segment.source_index
                            for segment in source.filtered_transcript.excluded
                            if range_index in segment.range_indices
                        ],
                    }
                    for range_index, exclusion in enumerate(source.exclusion_lookup.ranges)
                ]
            )
        elif args.command == "read-transcript":
            entries = read_transcript_entries(
                source,
                start=args.start,
                end=args.end,
                include_excluded=args.include_excluded,
            )
            _emit(_segment_page(entries, args.offset, args.limit))
        else:
            entries = read_chapter_entries(
                source, args.chapter_index, include_excluded=args.include_excluded
            )
            _emit(_segment_page(entries, args.offset, args.limit))
        return

    if args.command == "search-vault":
        with _vault(args.config) as vault:
            _emit(vault.search(args.query, scope=args.scope, cursor=args.cursor, limit=args.limit))
        return

    if args.command == "read-vault-note":
        with _vault(args.config) as vault:
            reference = vault.get_path_reference(args.path, args.revision)
            page = vault.read(
                reference.note_id,
                offset=args.offset,
                limit=args.limit,
                revision=args.revision,
            )
            page.pop("note_id")
            _emit(page)
        return

    source = _cache(args).get(args.source_id)
    with _vault(args.config) as vault:
        settings = vault.settings
        writer_settings = DraftWriterSettings(settings.root, settings.draft_folder)
        if args.command == "create-draft":
            references = []
            for path, revision, reason in args.related_note or ():
                note = vault.get_path_reference(path, revision)
                references.append({"note_id": note.note_id, "revision": revision, "reason": reason})
            related = verified_related_notes(vault, references)
            result = create_draft(
                writer_settings,
                title=args.title,
                contents=args.contents,
                source=source.metadata,
                timestamps=tuple(args.timestamp),
                related_notes=related,
            )
        else:
            target = vault.get_path_reference(args.target_path, args.target_revision)
            result = create_edit_suggestion(
                writer_settings,
                source=source.metadata,
                vault=vault,
                target_note_id=target.note_id,
                target_revision=args.target_revision,
                change_summary=args.change_summary,
                reasoning=args.reasoning,
                proposed_content=args.proposed_content,
                timestamps=tuple(args.timestamp),
            )
    _emit({"path": str(result.path)})


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, Path)):
        return str(value) if isinstance(value, Path) else value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
