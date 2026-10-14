import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from obsidian_ingest.extractor.metadata import fetch_metadata
from obsidian_ingest.extractor.transcript import print_transcript


def _run_mcp(
    vault_path: Path | None,
    draft_folder: str | None,
    cache_db: Path | None = None,
    *,
    config_path: Path | None = None,
) -> None:
    from obsidian_ingest.drafts import DraftWriterSettings
    from obsidian_ingest.mcp_server import create_server

    if config_path is not None:
        from obsidian_ingest.config import load_vault_settings

        if vault_path is not None or draft_folder is not None:
            raise ValueError("--config cannot be combined with --vault or --draft-folder")
        vault_settings = load_vault_settings(config_path)
        settings = DraftWriterSettings(vault_settings.root, vault_settings.draft_folder)
        server = create_server(settings, cache_db=cache_db, vault_settings=vault_settings)
        asyncio.run(server.run_stdio_async())
        return
    if vault_path is None:
        raise ValueError("--vault or --config is required")
    if not vault_path.is_dir():
        raise ValueError(f"Vault path must be an existing directory: {vault_path}")
    settings = DraftWriterSettings(vault_path, draft_folder or "00 Inbox/AI Drafts")
    server = create_server(settings, cache_db=cache_db)
    asyncio.run(server.run_stdio_async())


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
    vault_source = mcp_parser.add_mutually_exclusive_group(required=True)
    vault_source.add_argument(
        "--vault", type=Path, help="Existing Obsidian vault; vault reads disabled"
    )
    vault_source.add_argument(
        "--config", type=Path, help="Trusted TOML configuration enabling scoped vault reads"
    )
    mcp_parser.add_argument(
        "--cache-db",
        type=Path,
        help="Source SQLite database (default: XDG state directory, outside the vault)",
    )
    mcp_parser.add_argument(
        "--draft-folder",
        default=None,
        help="Vault-relative draft destination (default: 00 Inbox/AI Drafts)",
    )

    args = parser.parse_args()
    if args.command == "transcript":
        print_transcript(args.url)
    elif args.command == "inspect":
        from obsidian_ingest.inspection import format_source_inspection, inspect_source

        print(format_source_inspection(inspect_source(args.url)))
    elif args.command == "metadata":
        metadata = fetch_metadata(args.url)
        print(json.dumps(asdict(metadata), indent=2, default=_json_default))
    elif args.command == "mcp":
        _run_mcp(args.vault, args.draft_folder, args.cache_db, config_path=args.config)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
