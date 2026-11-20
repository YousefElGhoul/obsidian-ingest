import json
from dataclasses import replace

import pytest
from lxml import etree

from obsidian_ingest import responses
from obsidian_ingest.extractor import SourceKind
from obsidian_ingest.extractor.html_text import (
    Extraction,
    _Walker,
    extract_html,
    parse,
    sanitize,
    split_atom,
)
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExcludedRange,
    ExclusionLookup,
    ExclusionStatus,
)
from obsidian_ingest.extractor.providers.article.clipper import markdown_atoms
from obsidian_ingest.extractor.providers.youtube import normalize_youtube_metadata
from obsidian_ingest.extractor.providers.youtube.provider import YOUTUBE
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.filtering import filter_transcript
from obsidian_ingest.source import Source

URL = "https://example.com/post"


def _source(
    segments: tuple[TranscriptSegment, ...],
    *,
    kind: SourceKind = SourceKind.TEXT,
    chapters: tuple[Chapter, ...] = (),
    ranges: tuple[ExcludedRange, ...] = (),
) -> Source:
    metadata = replace(normalize_youtube_metadata({"id": "vid"}, URL), chapters=chapters)
    transcript = Transcript(metadata.source_id, "English", "en", False, segments, kind)
    lookup = ExclusionLookup(ranges, ExclusionStatus.COMPLETE, "test", ())
    return Source(metadata, transcript, lookup, filter_transcript(transcript, ranges))


def _render(xml: str, markup: str = "<html></html>") -> Extraction:
    result = Extraction()
    hints = sanitize(parse(markup), URL, result)
    _Walker(URL, hints, result).walk(etree.fromstring(f"<main>{xml}</main>"))
    return result


@pytest.mark.parametrize("text", ["x" * 28_000, "عربية" * 5_600], ids=["ascii", "arabic"])
def test_long_clipped_paragraph_can_be_read_in_full(text: str) -> None:
    atoms, _ = markdown_atoms(text)
    source = _source(tuple(TranscriptSegment(atom.text, kind=atom.kind) for atom in atoms))
    rows = []
    offset, token = 0, None
    while offset is not None:
        page = responses.source_page(source, offset=offset, read_token=token)
        rows.extend(page["segments"])
        offset, token = page["next_offset"], page["read_token"]
    assert [row[0] for row in rows] == list(range(len(atoms)))
    assert all(len(json.dumps(row[-1], ensure_ascii=False).encode()) <= 8_000 for row in rows)
    assert "".join(row[-1].removeprefix("(continued) ") for row in rows) == text


def test_html_atoms_count_json_escapes_and_utf8_bytes() -> None:
    text = 'ع"\\' * 4_500
    result = _render(f"<p>{text}</p>")
    assert len(result.atoms) > 1
    assert all(
        len(json.dumps(atom.text, ensure_ascii=False).encode()) <= 8_000 for atom in result.atoms
    )


def test_atom_splitting_prefers_line_then_sentence_and_preserves_whitespace() -> None:
    line = "x" * 4_000 + "\n"
    sentence = "y" * 6_000 + "."
    tail = " " + "z" * 5_000
    assert split_atom(line + sentence + tail) == [
        line,
        "(continued) " + sentence,
        "(continued) " + tail,
    ]


def test_hard_split_does_not_remove_the_escape_from_literal_markdown() -> None:
    result = _render("<p>" + "x" * 7_996 + "[link](javascript:…)</p>")
    assert result.atoms[1].text.startswith(r"(continued) \[")


def test_html_literal_markdown_stays_inert_but_generated_formatting_survives() -> None:
    result = _render(
        "<p>[x](javascript:…) and *literal* %% &lt;tag&gt; "
        '<ref target="/q(a) &lt;b&gt;">[label]</ref> <hi rend="#b">bold</hi></p>'
        '<graphic src="/image(a) &lt;b&gt;" alt="a]b"/>'
    )
    assert [atom.text for atom in result.atoms] == [
        (
            r"\[x\](javascript:…) and \*literal\* \%\% \<tag\> "
            r"[\[label\]](https://example.com/q%28a%29%20%3Cb%3E) **bold**"
        ),
        r"![a\]b](https://example.com/image%28a%29%20%3Cb%3E)",
    ]


@pytest.mark.parametrize(
    ("html_attributes", "body_attributes"), [(" hidden", ""), ("", ' style="display: none"')]
)
def test_hidden_document_or_body_has_no_text(html_attributes: str, body_attributes: str) -> None:
    markup = (
        f"<html{html_attributes}><body{body_attributes}><article><p>"
        + "A full paragraph of hidden text that should not be part of the article. " * 10
        + "</p></article></body></html>"
    )
    assert extract_html(markup, URL).atoms == []


def test_nested_list_keeps_parent_text_in_mixed_content_order() -> None:
    result = _render(
        '<list><item>before <hi rend="#b">bold</hi><list><item>nested</item></list>'
        'after <ref target="/x">link</ref><list><item>more</item></list>end</item></list>'
    )
    assert [atom.text for atom in result.atoms] == [
        "before **bold**",
        "  nested",
        "after [link](https://example.com/x) (continued)",
        "  more",
        "end (continued)",
    ]


def test_repeated_image_url_consumes_each_figures_caption_in_order() -> None:
    result = _render(
        '<graphic src="/image"/><graphic src="/image"/>',
        '<figure><img src="/image"><figcaption>One</figcaption></figure>'
        '<figure><img src="/image"><figcaption>Two</figcaption></figure>',
    )
    assert [atom.text for atom in result.atoms if atom.kind == "caption"] == ["One", "Two"]


def test_html_heading_paths_use_full_labels_and_remain_unique() -> None:
    result = _render(
        '<head rend="h2">Results (UK)</head><p>One</p>'
        '<head rend="h2">Results (UK)</head><p>Two</p>'
        '<head rend="h2">Results (UK) (2)</head><p>Three</p>'
        '<head rend="h3">Detail</head><p>Four</p>'
    )
    assert result.sections == [
        ("Results (UK)", 0),
        ("Results (UK) (2)", 1),
        ("Results (UK) (2) (2)", 2),
        ("Results (UK) (2) (2) › Detail", 3),
    ]
    assert [atom.section for atom in result.atoms] == [path for path, _ in result.sections]


def test_empty_retained_article_section_keeps_text_fields() -> None:
    source = _source(
        (TranscriptSegment("Sponsored", kind="paragraph", section="Ad"),),
        chapters=(Chapter("Ad", 0, 1),),
        ranges=(ExcludedRange(0, 1, "sponsored", "test"),),
    )
    for page in (responses.source_page(source), responses.section_page(source, 0)):
        assert page["fields"] == ["source_index", "section", "kind", "text"]
        assert page["segments"] == []


def test_video_chapter_title_preserves_heading_separator() -> None:
    title = "Part I › Setup"
    source = _source((), kind=SourceKind.TIMED, chapters=(Chapter(title, 0, 1),))
    assert responses.sections(source)[0]["title"] == title


@pytest.mark.parametrize("playlist_index", [True, False])
def test_youtube_decoder_rejects_boolean_playlist_index(playlist_index: bool) -> None:
    metadata = normalize_youtube_metadata({"id": "vid"}, URL)
    details = YOUTUBE.encode_details(metadata.provider_details)
    assert details is not None
    with pytest.raises(ValueError, match="playlist_index"):
        YOUTUBE.decode_details(details | {"playlist_index": playlist_index})
