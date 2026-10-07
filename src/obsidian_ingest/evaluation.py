"""Batch-inspect the content and schemas exposed by the local MCP source tools."""

import argparse
import asyncio
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PAGE_SIZE = 500


class EvaluationFailure(RuntimeError):
    pass


def parse_manifest(path: Path) -> tuple[str, ...]:
    ids = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        video_id = raw_line.split("#", 1)[0].strip()
        if not video_id:
            continue
        if not _VIDEO_ID.fullmatch(video_id):
            raise ValueError(f"Invalid video ID at manifest line {line_number}: {video_id}")
        ids.append(video_id)
    return tuple(ids)


def _result_data(result) -> dict[str, Any]:
    data = result.structured_content or {}
    if len(data) == 1 and "result" in data:
        return data["result"]
    return data


def _serialize_result(result) -> dict[str, Any]:
    return {
        "is_error": result.is_error,
        "structured_content": result.structured_content,
        "content": [item.model_dump(mode="json") for item in result.content],
    }


async def _call_tool(session, calls: list[dict[str, Any]], name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments=arguments)
    serialized = _serialize_result(result)
    calls.append({"name": name, "arguments": arguments, "result": serialized})
    if result.is_error:
        message = "\n".join(
            getattr(item, "text", "") for item in result.content if getattr(item, "text", "")
        )
        raise EvaluationFailure(f"Tool {name} failed: {message or 'no error details returned'}")
    return _result_data(result)


async def _read_all_pages(
    session,
    calls: list[dict[str, Any]],
    tool_name: str,
    base_arguments: dict[str, Any],
) -> tuple[list[int], int]:
    page_call_indices = []
    offset = 0
    segment_count = 0
    while True:
        page_call_indices.append(len(calls))
        page = await _call_tool(
            session,
            calls,
            tool_name,
            base_arguments | {"offset": offset, "limit": _PAGE_SIZE},
        )
        segment_count += len(page.get("segments", ()))
        next_offset = page.get("next_offset")
        if next_offset is None:
            return page_call_indices, segment_count
        if (
            isinstance(next_offset, bool)
            or not isinstance(next_offset, int)
            or next_offset <= offset
        ):
            raise EvaluationFailure(f"Tool {tool_name} returned an invalid next_offset")
        offset = next_offset


async def evaluate_video(session, video_id: str, tool_schemas: list[dict]) -> dict[str, Any]:
    url = f"https://www.youtube.com/watch?v={video_id}"
    report: dict[str, Any] = {
        "video_id": video_id,
        "url": url,
        "status": "failed",
        "tool_schemas": tool_schemas,
        "calls": [],
    }
    calls = report["calls"]
    try:
        ingested = await _call_tool(session, calls, "ingest_source", {"url": url})
        source_id = ingested["source_id"]
        report["source_id"] = source_id
        report["ingest_overview"] = ingested.get("overview")
        report["overview"] = await _call_tool(
            session, calls, "get_source_overview", {"source_id": source_id}
        )
        report["chapters"] = await _call_tool(
            session, calls, "list_chapters", {"source_id": source_id}
        )
        report["exclusions"] = await _call_tool(
            session, calls, "list_exclusions", {"source_id": source_id}
        )
        (
            report["transcript_page_call_indices"],
            report["transcript_segment_count"],
        ) = await _read_all_pages(session, calls, "read_transcript", {"source_id": source_id})
        chapters = report["chapters"]
        if chapters:
            first_chapter_index = chapters[0]["index"]
            call_indices, segment_count = await _read_all_pages(
                session,
                calls,
                "read_chapter",
                {"source_id": source_id, "chapter_index": first_chapter_index},
            )
            report["chapter_probe"] = {
                "chapter_index": first_chapter_index,
                "call_indices": call_indices,
                "segment_count": segment_count,
            }
        else:
            report["chapter_probe"] = {"skipped": "No creator chapters were provided"}
        report["status"] = "success"
    except Exception as error:  # noqa: BLE001 - record a per-video tool failure and continue.
        report["error"] = f"{type(error).__name__}: {error}"
    return report


def write_report(output_dir: Path, report: dict[str, Any]) -> Path:
    video_id = report["video_id"]
    destination = output_dir / f"mcp_eval_{video_id}.json"
    if report["status"] != "success" and destination.exists():
        destination = output_dir / f"mcp_eval_{video_id}.failed.json"

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_dir,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as report_file:
            temporary_path = Path(report_file.name)
            json.dump(report, report_file, ensure_ascii=False, indent=2)
            report_file.write("\n")
            report_file.flush()
            os.fsync(report_file.fileno())
        os.replace(temporary_path, destination)
    except BaseException:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass
        raise
    return destination


async def run_batch(video_ids: tuple[str, ...], output_dir: Path, delay_seconds: float) -> int:
    with TemporaryDirectory(prefix="obsidian-ingest-evaluation-") as temporary_vault:
        server = StdioServerParameters(
            command="uv",
            args=[
                "run",
                "--directory",
                str(ROOT),
                "obsidian-ingest",
                "mcp",
                "--vault",
                temporary_vault,
            ],
            cwd=str(ROOT),
        )
        async with (
            stdio_client(server) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            tool_schemas = [tool.model_dump(mode="json") for tool in tools.tools]
            failures = 0
            for index, video_id in enumerate(video_ids):
                if index and delay_seconds:
                    await asyncio.sleep(delay_seconds)
                print(f"[{index + 1}/{len(video_ids)}] Inspecting {video_id}", file=sys.stderr)
                report = await evaluate_video(session, video_id, tool_schemas)
                try:
                    path = write_report(output_dir, report)
                except OSError as error:
                    failures += 1
                    print(f"FAILED {video_id}: could not save report: {error}", file=sys.stderr)
                    continue
                if report["status"] == "success":
                    print(
                        f"SUCCESS {video_id}: {report['transcript_segment_count']} retained "
                        f"segments, report: {path}",
                        file=sys.stderr,
                    )
                else:
                    failures += 1
                    print(f"FAILED {video_id}: {report['error']}; report: {path}", file=sys.stderr)
            print(
                f"Summary: {len(video_ids) - failures} succeeded, {failures} failed.",
                file=sys.stderr,
            )
            return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save the source-tool content exposed to an agent over local MCP."
    )
    parser.add_argument("manifest", nargs="?", type=Path, default=ROOT / "videos.txt")
    parser.add_argument("output_dir", nargs="?", type=Path, default=ROOT / "outputs_archive")
    parser.add_argument("delay_seconds", nargs="?", type=float, default=10.0)
    args = parser.parse_args()

    if not args.manifest.is_file() or not os.access(args.manifest, os.R_OK):
        parser.error(f"Manifest is not a readable file: {args.manifest}")
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        parser.error(f"Could not create output directory {args.output_dir}: {error}")
    if not args.output_dir.is_dir() or not os.access(args.output_dir, os.W_OK):
        parser.error(f"Output directory must exist and be writable: {args.output_dir}")
    if not math.isfinite(args.delay_seconds) or args.delay_seconds < 0:
        parser.error("Delay must be finite and nonnegative")
    try:
        video_ids = parse_manifest(args.manifest)
    except ValueError as error:
        parser.error(str(error))
    if not video_ids:
        print("Manifest has no video IDs.", file=sys.stderr)
        return 0
    return asyncio.run(run_batch(video_ids, args.output_dir, args.delay_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
