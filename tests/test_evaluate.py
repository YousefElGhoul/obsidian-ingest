import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from obsidian_ingest.evaluation import evaluate_video, main, parse_manifest, write_report


def _result(data=None, *, error: str | None = None):
    content = [
        SimpleNamespace(
            text=error,
            model_dump=lambda mode="json": {"type": "text", "text": error},
        )
    ]
    return SimpleNamespace(
        is_error=error is not None,
        structured_content=None if error else {"result": data},
        content=content if error else [],
    )


class FakeSession:
    def __init__(self) -> None:
        self.calls = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        if name == "ingest_source":
            return _result({"source_id": "source-1", "overview": {"title": "Lesson"}})
        if name == "get_source_overview":
            return _result({"title": "Lesson", "sponsor_filtering": {"status": "complete"}})
        if name == "list_chapters":
            return _result([{"index": 0, "title": "First", "start": 0, "end": 10}])
        if name == "list_exclusions":
            return _result([{"start": 4, "end": 5, "source_indices": [1]}])
        if name == "read_transcript":
            if arguments["offset"] == 0:
                return _result(
                    {
                        "segments": [{"source_index": 0, "text": "One"}],
                        "next_offset": 1,
                    }
                )
            return _result({"segments": [{"source_index": 2, "text": "Two"}], "next_offset": None})
        if name == "read_chapter":
            return _result({"segments": [{"source_index": 0, "text": "One"}], "next_offset": None})
        raise AssertionError(f"Unexpected tool call: {name}")


def test_evaluate_video_records_all_source_tool_content_without_writing_drafts() -> None:
    session = FakeSession()
    report = asyncio.run(evaluate_video(session, "abcdefghijk", [{"name": "tool"}]))

    assert report["status"] == "success"
    assert report["transcript_segment_count"] == 2
    assert report["chapter_probe"]["segment_count"] == 1
    assert [name for name, _ in session.calls] == [
        "ingest_source",
        "get_source_overview",
        "list_chapters",
        "list_exclusions",
        "read_transcript",
        "read_transcript",
        "read_chapter",
    ]
    assert all(name != "create_draft" for name, _ in session.calls)
    assert session.calls[0][1] == {"url": "https://www.youtube.com/watch?v=abcdefghijk"}
    assert all("include_excluded" not in arguments for _, arguments in session.calls)
    assert (
        report["calls"][4]["result"]["structured_content"]["result"]["segments"][0]["text"] == "One"
    )


def test_evaluate_video_records_and_stops_on_tool_errors() -> None:
    class FailedSession:
        async def call_tool(self, name, arguments):
            return _result(error="simulated ingestion failure")

    report = asyncio.run(evaluate_video(FailedSession(), "abcdefghijk", []))

    assert report["status"] == "failed"
    assert "simulated ingestion failure" in report["error"]
    assert [call["name"] for call in report["calls"]] == ["ingest_source"]


def test_manifest_parsing_ignores_comments_and_rejects_bad_ids(tmp_path: Path) -> None:
    manifest = tmp_path / "videos.txt"
    manifest.write_text("# heading\nabcdefghijk # note\n\nABCDEFGHIJK\n", encoding="utf-8")
    assert parse_manifest(manifest) == ("abcdefghijk", "ABCDEFGHIJK")

    manifest.write_text("not-a-video-id\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        parse_manifest(manifest)


def test_failure_report_does_not_replace_previous_success(tmp_path: Path) -> None:
    success = {"video_id": "abcdefghijk", "status": "success"}
    previous = write_report(tmp_path, success)
    failed = {"video_id": "abcdefghijk", "status": "failed", "error": "offline"}

    failure_path = write_report(tmp_path, failed)

    assert (
        previous.read_text(encoding="utf-8")
        == json.dumps(success, ensure_ascii=False, indent=2) + "\n"
    )
    assert failure_path.name == "mcp_eval_abcdefghijk.failed.json"
    assert json.loads(failure_path.read_text(encoding="utf-8")) == failed


def test_main_creates_output_directory_for_empty_manifest(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest = tmp_path / "empty.txt"
    manifest.write_text("# no ids\n", encoding="utf-8")
    output = tmp_path / "reports"
    monkeypatch.setattr(sys, "argv", ["evaluate.py", str(manifest), str(output), "0"])

    assert main() == 0
    assert output.is_dir()
    assert "Manifest has no video IDs" in capsys.readouterr().err


def test_local_stdio_server_exposes_tools_without_source_or_agent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    server_code = (
        "import sys; from pathlib import Path; from obsidian_ingest.cli import _run_mcp; "
        "_run_mcp(Path(sys.argv[1]), '00 Inbox/AI Drafts')"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", server_code, str(vault)],
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    async def list_server_tools() -> set[str]:
        async with (
            stdio_client(params) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            return {tool.name for tool in tools.tools}

    assert asyncio.run(list_server_tools()) == {
        "ingest_source",
        "get_source_overview",
        "list_chapters",
        "list_exclusions",
        "read_transcript",
        "read_chapter",
        "create_draft",
    }
