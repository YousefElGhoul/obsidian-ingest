import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import evaluate as evaluation
from evaluate import (
    evaluate_source,
    main,
    parse_manifest,
    report_stem,
    write_report,
)

_URL = "https://www.youtube.com/watch?v=abcdefghijk"


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


def _text_result(data):
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return SimpleNamespace(
        is_error=False,
        structured_content=None,
        content=[
            SimpleNamespace(
                text=text, model_dump=lambda mode="json": {"type": "text", "text": text}
            )
        ],
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
                return _text_result(
                    {
                        "segments": [{"source_index": 0, "text": "One"}],
                        "offset": 0,
                        "limit": evaluation._PAGE_SIZE,
                        "total_segments": 2,
                        "next_offset": 1,
                    }
                )
            return _text_result(
                {
                    "segments": [{"source_index": 2, "text": "Two"}],
                    "offset": 1,
                    "limit": evaluation._PAGE_SIZE,
                    "total_segments": 2,
                    "next_offset": None,
                }
            )
        if name == "read_chapter":
            return _text_result(
                {
                    "segments": [{"source_index": 0, "text": "One"}],
                    "offset": 0,
                    "limit": evaluation._PAGE_SIZE,
                    "total_segments": 1,
                    "next_offset": None,
                }
            )
        raise AssertionError(f"Unexpected tool call: {name}")


def test_evaluate_video_records_all_source_tool_content_without_writing_drafts() -> None:
    session = FakeSession()
    report = asyncio.run(evaluate_source(session, _URL, [{"name": "tool"}]))

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
    assert session.calls[0][1] == {"url": _URL}
    assert report["url"] == _URL
    assert all("include_excluded" not in arguments for _, arguments in session.calls)
    assert all(
        arguments["limit"] == 200
        for name, arguments in session.calls
        if name in {"read_transcript", "read_chapter"}
    )
    assert session.calls[5][1]["offset"] == 1
    transcript_result = report["calls"][4]["result"]
    assert transcript_result["structured_content"] is None
    assert json.loads(transcript_result["content"][0]["text"])["segments"][0]["text"] == "One"


def test_evaluate_video_records_and_stops_on_tool_errors() -> None:
    class FailedSession:
        async def call_tool(self, name, arguments):
            return _result(error="simulated ingestion failure")

    report = asyncio.run(evaluate_source(FailedSession(), _URL, []))

    assert report["status"] == "failed"
    assert "simulated ingestion failure" in report["error"]
    assert [call["name"] for call in report["calls"]] == ["ingest_source"]


class ScriptedSession(FakeSession):
    """A FakeSession whose replies can be replaced per tool name."""

    def __init__(self, **replies) -> None:
        super().__init__()
        self.replies = replies

    async def call_tool(self, name: str, arguments: dict):
        if name in self.replies:
            self.calls.append((name, arguments))
            return self.replies[name](arguments)
        return await super().call_tool(name, arguments)


def test_evaluation_without_chapters_skips_the_chapter_probe() -> None:
    session = ScriptedSession(list_chapters=lambda _: _result([]))
    report = asyncio.run(evaluate_source(session, _URL, []))
    assert report["status"] == "success"
    assert report["chapter_probe"] == {"skipped": "No creator chapters were provided"}
    assert "read_chapter" not in [name for name, _ in session.calls]


@pytest.mark.parametrize("next_offset", [0, True, "1"], ids=["not-advancing", "bool", "string"])
def test_invalid_transcript_paging_is_a_recorded_failure(next_offset) -> None:
    page = {"segments": [], "next_offset": next_offset}
    session = ScriptedSession(read_transcript=lambda _: _text_result(page))
    report = asyncio.run(evaluate_source(session, _URL, []))
    assert report["status"] == "failed"
    assert (
        report["error"] == "EvaluationFailure: Tool read_transcript returned an invalid next_offset"
    )
    assert [name for name, _ in session.calls].count("read_transcript") == 1


def test_unparseable_tool_text_is_recorded_not_raised() -> None:
    garbage = SimpleNamespace(
        is_error=False,
        structured_content=None,
        content=[SimpleNamespace(text="not json", model_dump=lambda mode="json": {"text": "x"})],
    )
    session = ScriptedSession(ingest_source=lambda _: garbage)
    report = asyncio.run(evaluate_source(session, _URL, []))
    assert report["status"] == "failed"
    assert report["error"].startswith("KeyError")
    assert report["calls"][0]["result"]["content"] == [{"text": "x"}]


def test_failed_report_write_leaves_no_temporary_file(tmp_path: Path, monkeypatch) -> None:
    def broken_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(evaluation.json, "dump", broken_dump)
    with pytest.raises(OSError, match="disk full"):
        write_report(tmp_path, {"url": _URL, "status": "success"})
    assert list(tmp_path.iterdir()) == []


def test_batch_reports_each_source_and_returns_failure_when_any_failed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    failing_url = "https://youtu.be/failing"
    unsaved_url = "https://youtu.be/unsaved"

    class BatchSession(FakeSession):
        async def initialize(self) -> None:
            pass

        async def list_tools(self):
            tool = SimpleNamespace(model_dump=lambda mode="json": {"name": "ingest_source"})
            return SimpleNamespace(tools=[tool])

        async def call_tool(self, name: str, arguments: dict):
            if arguments.get("url") == failing_url:
                return _result(error="simulated failure")
            return await super().call_tool(name, arguments)

    class FakeContext:
        def __init__(self, value) -> None:
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *exc) -> bool:
            return False

    servers = []

    def fake_stdio_client(server):
        servers.append(server)
        return FakeContext(("read", "write"))

    real_write = evaluation.write_report

    def write_or_fail(output_dir, report):
        if report["url"] == unsaved_url:
            raise OSError("read-only")
        return real_write(output_dir, report)

    monkeypatch.setattr(evaluation, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(evaluation, "ClientSession", lambda r, w: FakeContext(BatchSession()))
    monkeypatch.setattr(evaluation, "write_report", write_or_fail)

    urls = (_URL, failing_url, unsaved_url)
    assert asyncio.run(evaluation.run_batch(urls, tmp_path, 0.001)) == 1

    (server,) = servers
    assert server.args[:2] == ["run", "--directory"] and "--vault" in server.args
    vault = Path(server.args[server.args.index("--vault") + 1])
    state = Path(server.env["XDG_STATE_HOME"])
    assert state.parent == vault.parent and state != vault
    assert not state.parent.exists()  # the temporary cache is removed with the vault
    assert server.env["PATH"] == os.environ["PATH"]
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        f"{report_stem(url)}.json" for url in (_URL, failing_url)
    )
    saved = json.loads((tmp_path / f"{report_stem(_URL)}.json").read_text(encoding="utf-8"))
    assert saved["tool_schemas"] == [{"name": "ingest_source"}]
    err = capsys.readouterr().err
    assert f"SUCCESS {_URL}: 2 retained segments" in err
    assert f"FAILED {failing_url}: EvaluationFailure: Tool ingest_source failed" in err
    assert f"FAILED {unsaved_url}: could not save report: read-only" in err
    assert "Summary: 1 succeeded, 2 failed." in err


def test_main_runs_the_batch_for_a_nonempty_manifest(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "videos.txt"
    manifest.write_text(f"{_URL}\n", encoding="utf-8")
    output = tmp_path / "reports"
    received = []

    async def fake_batch(urls, output_dir, delay_seconds):
        received.append((urls, output_dir, delay_seconds))
        return 0

    monkeypatch.setattr(evaluation, "run_batch", fake_batch)
    monkeypatch.setattr(sys, "argv", ["evaluate.py", str(manifest), str(output), "2.5"])
    assert main() == 0
    assert received == [((_URL,), output, 2.5)]


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing-manifest", "Manifest is not a readable file"),
        ("output-is-file", "Could not create output directory"),
        ("output-not-writable", "Output directory must exist and be writable"),
        ("negative-delay", "Delay must be finite and nonnegative"),
        ("infinite-delay", "Delay must be finite and nonnegative"),
    ],
)
def test_main_rejects_unusable_arguments(tmp_path: Path, monkeypatch, capsys, case, message):
    manifest = tmp_path / "videos.txt"
    manifest.write_text(f"{_URL}\n", encoding="utf-8")
    output = tmp_path / "reports"
    delay = "0"
    if case == "missing-manifest":
        manifest = tmp_path / "missing.txt"
    elif case == "output-is-file":
        (tmp_path / "blocker").write_text("file", encoding="utf-8")
        output = tmp_path / "blocker/reports"
    elif case == "output-not-writable":
        real_access = os.access
        monkeypatch.setattr(
            evaluation.os,
            "access",
            lambda path, mode: False if Path(path) == output else real_access(path, mode),
        )
    else:
        delay = "-1" if case == "negative-delay" else "inf"
    monkeypatch.setattr(evaluation, "run_batch", lambda *args: pytest.fail("must not run"))
    monkeypatch.setattr(sys, "argv", ["evaluate.py", str(manifest), str(output), delay])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err


def test_manifest_parsing_keeps_url_order_and_skips_comments(tmp_path: Path) -> None:
    manifest = tmp_path / "videos.txt"
    manifest.write_text(
        f"# heading\n  {_URL}  \n\n   # indented comment\nhttps://youtu.be/ABCDEFGHIJK?t=5#frag\n",
        encoding="utf-8",
    )

    assert parse_manifest(manifest) == (_URL, "https://youtu.be/ABCDEFGHIJK?t=5#frag")


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("abcdefghijk", "Invalid source URL"),
        ("www.youtube.com/watch?v=abcdefghijk", "Invalid source URL"),
        ("ftp://example.com/video", "Invalid source URL"),
        ("https:///watch?v=abcdefghijk", "Invalid source URL"),
        (f"{_URL} # note", "Invalid source URL"),
        ("https://[bad/watch", "Invalid source URL"),
        (_URL, "Duplicate source URL"),
    ],
)
def test_manifest_parsing_rejects_malformed_lines_by_line_number(
    tmp_path: Path, line: str, message: str
) -> None:
    manifest = tmp_path / "videos.txt"
    manifest.write_text(f"# heading\n{_URL}\n\n{line}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=f"{message} at manifest line 4"):
        parse_manifest(manifest)


def test_report_stem_is_stable_and_filesystem_safe() -> None:
    stem = report_stem(_URL)

    assert stem == report_stem(_URL)
    assert stem != report_stem("https://youtu.be/abcdefghijk")
    assert stem.startswith("mcp_eval_") and stem.replace("_", "").isalnum()


def test_failure_report_does_not_replace_previous_success(tmp_path: Path) -> None:
    success = {"url": _URL, "status": "success"}
    previous = write_report(tmp_path, success)
    failed = {"url": _URL, "status": "failed", "error": "offline"}

    failure_path = write_report(tmp_path, failed)

    assert (
        previous.read_text(encoding="utf-8")
        == json.dumps(success, ensure_ascii=False, indent=2) + "\n"
    )
    assert previous.name == f"{report_stem(_URL)}.json"
    assert failure_path.name == f"{report_stem(_URL)}.failed.json"
    assert json.loads(failure_path.read_text(encoding="utf-8")) == failed


def test_main_creates_output_directory_for_empty_manifest(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    manifest = tmp_path / "empty.txt"
    manifest.write_text("# no urls\n", encoding="utf-8")
    output = tmp_path / "reports"
    monkeypatch.setattr(sys, "argv", ["evaluate.py", str(manifest), str(output), "0"])

    assert main() == 0
    assert output.is_dir()
    assert "Manifest has no source URLs" in capsys.readouterr().err


def test_main_rejects_malformed_manifest_line(tmp_path: Path, monkeypatch, capsys) -> None:
    manifest = tmp_path / "videos.txt"
    manifest.write_text("# old format\nabcdefghijk\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluate.py", str(manifest), str(tmp_path / "reports"), "0"])

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 2
    assert "Invalid source URL at manifest line 2: abcdefghijk" in capsys.readouterr().err


def test_local_stdio_server_exposes_tools_without_source_or_agent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    server_code = (
        "import sys; from pathlib import Path; from obsidian_ingest.cli import _run_mcp; "
        "_run_mcp(Path(sys.argv[1]), '00 Inbox/AI Drafts', Path(sys.argv[2]))"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", server_code, str(vault), str(tmp_path / "cache.db")],
        cwd=str(Path(__file__).resolve().parents[1]),
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "skills/obsidian-ingest/src"),
        },
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
        "get_thumbnail",
        "create_draft",
    }
