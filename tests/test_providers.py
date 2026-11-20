"""The provider boundary, cited-row validation, and citation invalidation."""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from obsidian_ingest import responses, source_cache
from obsidian_ingest.extractor import Provider
from obsidian_ingest.extractor.providers import (
    Citation,
    SourceProvider,
    get_provider,
    registry,
    resolve_url,
    supported_provider,
)
from obsidian_ingest.extractor.transcript import TranscriptSegment
from obsidian_ingest.filtering import filter_source_transcript

PACKAGE = Path(responses.__file__).parent
PROVIDERS = PACKAGE / "extractor" / "providers"
PROVIDER_NAMES = {member.name for member in Provider}


def _shared_modules() -> list[Path]:
    """Every module outside the provider packages, which must stay provider-neutral."""
    return [path for path in PACKAGE.rglob("*.py") if PROVIDERS not in path.parents]


@pytest.mark.parametrize("path", _shared_modules(), ids=lambda path: path.name)
def test_shared_code_never_names_a_specific_provider(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("obsidian_ingest.extractor.providers."), (
                f"{path.name} imports provider package {node.module}"
            )
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert not (node.value.id == "Provider" and node.attr in PROVIDER_NAMES), (
                f"{path.name} compares against Provider.{node.attr}"
            )


def test_every_provider_implements_the_whole_protocol() -> None:
    members = {
        name
        for name in vars(SourceProvider)
        if not name.startswith("_") and name not in {"name", "kind", "supported"}
    } | set(SourceProvider.__annotations__)
    for provider in registry():
        missing = {name for name in members if not hasattr(provider, name)}
        assert not missing, f"{provider.name} lacks {sorted(missing)}"


def test_registry_resolves_supported_and_recognized_providers() -> None:
    assert {provider.name for provider in registry()} >= {Provider.YOUTUBE}
    assert resolve_url("https://youtu.be/abc").name == Provider.YOUTUBE
    assert supported_provider("https://youtu.be/abc").supported
    assert get_provider("youtube") is resolve_url("https://youtu.be/abc")
    for url in ("https://www.instagram.com/reel/x/", "https://vm.tiktok.com/x/"):
        assert not resolve_url(url).supported
        with pytest.raises(ValueError, match="not supported"):
            supported_provider(url)
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("nope")


def test_youtube_details_round_trip_and_reject_malformed_data(sample_source) -> None:
    youtube = get_provider(Provider.YOUTUBE)
    details = sample_source.metadata.provider_details
    assert youtube.decode_details(youtube.encode_details(details)) == details
    encoded = youtube.encode_details(details)
    for broken in (
        encoded | {"extra": 1},
        {key: value for key, value in encoded.items() if key != "is_short"},
        encoded | {"is_short": "no"},
        encoded | {"playlist_index": True},
    ):
        with pytest.raises(ValueError):
            youtube.decode_details(broken)


# --- cite: indices of rows the agent read --------------------------------------------


def test_cite_renders_provider_citation_and_ignores_duplicates(sample_source) -> None:
    citation = responses.cite_rows(sample_source, [2, 0, 2])
    assert citation == Citation(
        "Relevant timestamps", "[00:00](https://www.youtube.com/watch?v=video-123&t=0s)"
    )
    assert responses.cite_rows(sample_source, None) is None
    assert responses.cite_rows(sample_source, []) is None


@pytest.mark.parametrize(
    ("cite", "message"),
    [
        ([-1], "rows 0 to 3"),
        ([4], "rows 0 to 3"),
        ([True], "source_index numbers"),
        ([1.0], "source_index numbers"),
        (["1"], "source_index numbers"),
        ([3], "excluded"),
        ("0", "must be a list"),
        (list(range(201)), "at most 200"),
    ],
)
def test_cite_rejects_unknown_excluded_and_malformed_rows(sample_source, cite, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message) as caught:
        responses.cite_rows(sample_source, cite)
    assert str(caught.value).startswith("INVALID_ARGUMENT: ")


# --- Citations are invalidated when the capture changes -------------------------------


def test_refresh_between_reading_and_writing_invalidates_the_read(
    tmp_path, monkeypatch, sample_source, cli_runner
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "settings.toml"
    config.write_text(f'[vault]\nroot = "{vault}"\nallowed = ["*"]\n', encoding="utf-8")
    db = ("--cache-db", str(tmp_path / "cache.db"))
    captures = [sample_source]
    monkeypatch.setattr(
        source_cache,
        "ingest_source",
        lambda url, exclude_sponsors=True, previous=None: captures[-1],
    )
    source_id = cli_runner.ok("ingest-source", "https://youtu.be/video-123", *db)["source_id"]

    def read_all() -> str:
        token, offset = None, 0
        while offset is not None:
            args = ("read-source", source_id, "--offset", str(offset), *db)
            page = cli_runner.ok(*args, *(("--read-token", token) if token else ()))
            token, offset = page["read_token"], page["next_offset"]
        return token

    token = read_all()
    changed = replace(
        sample_source.transcript,
        segments=(TranscriptSegment("Rewritten first atom", 0, 1),)
        + sample_source.transcript.segments[1:],
    )
    captures.append(
        replace(
            sample_source,
            transcript=changed,
            filtered_transcript=filter_source_transcript(
                sample_source.metadata, changed, sample_source.exclusion_lookup
            ),
        )
    )
    refreshed = cli_runner.ok("ingest-source", "https://youtu.be/video-123", "--refresh", *db)
    assert refreshed["source_id"] == source_id

    draft = ("create-draft", source_id, "# Note\n\nBody.", "--config", str(config), *db)
    stale = cli_runner.fail(*draft, "--cite", "0", "--read-token", token)
    assert stale["code"] == "INCOMPLETE_READ" and "source changed" in stale["message"]
    assert not (vault / "00 Inbox").exists()

    created = cli_runner.ok(*draft, "--cite", "0", "--read-token", read_all())
    assert Path(created["path"]).exists()
