import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from obsidian_ingest.extractor.metadata import fetch_metadata
from obsidian_ingest.extractor.transcript import print_transcript


def _run_mcp(vault_path: Path, draft_folder: str) -> None:
    from obsidian_ingest.drafts import DraftWriterSettings
    from obsidian_ingest.mcp_server import create_server

    if not vault_path.is_dir():
        raise ValueError(f"Vault path must be an existing directory: {vault_path}")
    server = create_server(DraftWriterSettings(vault_path, draft_folder))
    asyncio.run(server.run_stdio_async())


def main() -> None:
    parser = argparse.ArgumentParser(prog="obsidian-ingest")
    subparsers = parser.add_subparsers(dest="command", required=True)

    transcript_parser = subparsers.add_parser("transcript", help="Print source captions")
    transcript_parser.add_argument("url", help="Video URL")

    inspect_parser = subparsers.add_parser(
        "inspect", help="Print the legacy SponsorBlock-aware baseline inspection"
    )
    inspect_parser.add_argument("url", help="Video URL")

    metadata_parser = subparsers.add_parser("metadata", help="Print normalized source metadata")
    metadata_parser.add_argument("url", help="Video URL")

    mcp_parser = subparsers.add_parser("mcp", help="Run the local stdio MCP server")
    mcp_parser.add_argument("--vault", type=Path, required=True, help="Existing Obsidian vault")
    mcp_parser.add_argument(
        "--draft-folder",
        default="00 Inbox/AI Drafts",
        help="Vault-relative draft destination (default: 00 Inbox/AI Drafts)",
    )

    args = parser.parse_args()
    if args.command == "transcript":
        print_transcript(args.url)
    elif args.command == "inspect":
        from obsidian_ingest.segmentation import format_segmentation, inspect_source

        print(format_segmentation(inspect_source(args.url)))
    elif args.command == "metadata":
        metadata = fetch_metadata(args.url)
        print(json.dumps(asdict(metadata), indent=2, default=_json_default))
    elif args.command == "mcp":
        _run_mcp(args.vault, args.draft_folder)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
