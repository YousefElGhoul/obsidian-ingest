"""Record acquisition/navigation over local MCP without agent curation or draft publication.

A repository developer tool: it launches the skill in this checkout and is not shipped
with the skill.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

# Default manifest/report locations and the launched server are this repository checkout.
ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "obsidian-ingest"
_PAGE_SIZE = 200


class EvaluationFailure(RuntimeError):
    """A tool error or malformed pagination result prevents completing a source probe."""


def parse_manifest(path: Path) -> tuple[str, ...]:
    """Return source URLs in file order: one absolute http(s) URL per line.

    Blank lines and lines whose first non-space character is `#` are ignored. Inline
    comments are not supported because `#` may belong to a URL fragment.
    """
    urls: list[str] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not _is_source_url(line):
            raise ValueError(f"Invalid source URL at manifest line {line_number}: {line}")
        if line in urls:
            raise ValueError(f"Duplicate source URL at manifest line {line_number}: {line}")
        urls.append(line)
    return tuple(urls)


def _is_source_url(value: str) -> bool:
    if any(character.isspace() for character in value):
        return False
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(hostname)


def report_stem(url: str) -> str:
    """Return a stable, filesystem-safe report filename stem for a source URL."""
    return f"mcp_eval_{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}"


# MCP response recording and source probes


def _result_data(result) -> dict[str, Any]:
    """Accept structured MCP data or its text fallback, unwrapping the result envelope."""
    data = result.structured_content or {}
    if not data:
        text = next(
            (getattr(item, "text", "") for item in result.content if getattr(item, "text", "")),
            "",
        )
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
    if len(data) == 1 and "result" in data:
        return data["result"]
    return data


def _serialize_result(result) -> dict[str, Any]:
    """Retain both MCP representations so reports show what clients actually received."""
    return {
        "is_error": result.is_error,
        "structured_content": result.structured_content,
        "content": [item.model_dump(mode="json") for item in result.content],
    }


async def _call_tool(session, calls: list[dict[str, Any]], name: str, arguments: dict) -> dict:
    """Record a call before raising EvaluationFailure for an MCP tool error."""
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
    """Read every page, returning report-call indices and the total segment count."""
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
        # Reject non-progressing cursors so a malformed server cannot loop forever.
        if (
            isinstance(next_offset, bool)
            or not isinstance(next_offset, int)
            or next_offset <= offset
        ):
            raise EvaluationFailure(f"Tool {tool_name} returned an invalid next_offset")
        offset = next_offset


async def evaluate_source(session, url: str, tool_schemas: list[dict]) -> dict[str, Any]:
    """Record one source's navigation, retaining partial calls when any probe fails."""
    report: dict[str, Any] = {
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
            session, calls, "list_sections", {"source_id": source_id}
        )
        report["exclusions"] = await _call_tool(
            session, calls, "list_exclusions", {"source_id": source_id}
        )
        (
            report["transcript_page_call_indices"],
            report["transcript_segment_count"],
        ) = await _read_all_pages(session, calls, "read_source", {"source_id": source_id})
        chapters = report["chapters"]
        if chapters:
            # One chapter probes the scoped read surface; the full transcript is already recorded.
            first_section_index = chapters[0]["index"]
            call_indices, segment_count = await _read_all_pages(
                session,
                calls,
                "read_section",
                {"source_id": source_id, "section_index": first_section_index},
            )
            report["chapter_probe"] = {
                "section_index": first_section_index,
                "call_indices": call_indices,
                "segment_count": segment_count,
            }
        else:
            report["chapter_probe"] = {"skipped": "No creator chapters were provided"}
        report["evidence_summary"] = evidence_summary(report)
        report["status"] = "success"
    except Exception as error:  # noqa: BLE001 - record a per-video tool failure and continue.
        report["error"] = f"{type(error).__name__}: {error}"
    return report


def evidence_summary(report: dict[str, Any]) -> dict[str, Any]:
    """A compact view of how a source was acquired, for comparing runs at a glance."""
    overview = report.get("overview") or {}
    diagnostics = overview.get("diagnostics") or {}
    evidence = diagnostics.get("evidence") or {}
    quality = evidence.get("quality") or {}
    http = evidence.get("http") or {}
    exclusions = overview.get("exclusions") or {}
    return {
        "kind": evidence.get("kind"),
        "method": evidence.get("method"),
        "attempts": [
            {key: attempt.get(key) for key in ("method", "status", "reason", "words")}
            for attempt in evidence.get("attempts") or []
        ],
        "quality": quality.get("status"),
        "warnings": quality.get("warnings") or [],
        "words": quality.get("words"),
        "sections": overview.get("section_count"),
        "segments": report.get("transcript_segment_count"),
        "excluded_segments": exclusions.get("excluded_segments"),
        "language": overview.get("language"),
        "metadata_provenance": evidence.get("metadata_provenance"),
        "encoding": http.get("encoding"),
        "bytes": http.get("bytes"),
        "same_content_as": diagnostics.get("same_content_as") or [],
    }


# Report publication and batch command


def write_report(output_dir: Path, report: dict[str, Any]) -> Path:
    """Atomically publish JSON, keeping an existing primary report when a probe fails."""
    stem = report_stem(report["url"])
    destination = output_dir / f"{stem}.json"
    if report["status"] != "success" and destination.exists():
        destination = output_dir / f"{stem}.failed.json"

    temporary_path = None
    try:
        # A sibling temporary file keeps replacement on the destination filesystem.
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


async def run_batch(urls: tuple[str, ...], output_dir: Path, delay_seconds: float) -> int:
    """Inspect URLs serially through a local server; return a nonzero status on any failure."""
    # Source tools need a configured vault, but evaluation must not use a real user's vault
    # or source cache. The MCP client passes the server only a minimal environment, so the
    # temporary cache location is passed explicitly.
    with TemporaryDirectory(prefix="obsidian-ingest-evaluation-") as temporary_root:
        temporary_vault = str(Path(temporary_root, "vault"))
        temporary_state = Path(temporary_root, "state")
        Path(temporary_vault).mkdir()
        temporary_state.mkdir()
        server = StdioServerParameters(
            command="uv",
            args=[
                "run",
                "--directory",
                str(SKILL_ROOT),
                "obsidian-ingest",
                "mcp",
                "--vault",
                temporary_vault,
            ],
            cwd=str(SKILL_ROOT),
            env={**get_default_environment(), "XDG_STATE_HOME": str(temporary_state)},
        )
        async with (
            stdio_client(server) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            tool_schemas = [tool.model_dump(mode="json") for tool in tools.tools]
            failures = 0
            for index, url in enumerate(urls):
                if index and delay_seconds:
                    await asyncio.sleep(delay_seconds)
                print(f"[{index + 1}/{len(urls)}] Inspecting {url}", file=sys.stderr)
                report = await evaluate_source(session, url, tool_schemas)
                try:
                    path = write_report(output_dir, report)
                except OSError as error:
                    failures += 1
                    print(f"FAILED {url}: could not save report: {error}", file=sys.stderr)
                    continue
                if report["status"] == "success":
                    summary = report["evidence_summary"]
                    how = (
                        f", {summary['method']} ({summary['quality']})" if summary["method"] else ""
                    )
                    print(
                        f"SUCCESS {url}: {report['transcript_segment_count']} retained "
                        f"segments{how}, report: {path}",
                        file=sys.stderr,
                    )
                else:
                    failures += 1
                    print(f"FAILED {url}: {report['error']}; report: {path}", file=sys.stderr)
            print(
                f"Summary: {len(urls) - failures} succeeded, {failures} failed.",
                file=sys.stderr,
            )
            return 1 if failures else 0


def main() -> int:
    """Validate manifest/output arguments and run the model-free MCP inspection batch."""
    parser = argparse.ArgumentParser(
        description=(
            "Save the source-tool content exposed to an agent over local MCP for each URL in "
            "a manifest: one http(s) URL per line; blank lines and full-line # comments allowed."
        )
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
        urls = parse_manifest(args.manifest)
    except ValueError as error:
        parser.error(str(error))
    if not urls:
        print("Manifest has no source URLs.", file=sys.stderr)
        return 0
    return asyncio.run(run_batch(urls, args.output_dir, args.delay_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
