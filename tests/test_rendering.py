import re
from dataclasses import replace

import pytest
from lxml import etree

from obsidian_ingest import responses
from obsidian_ingest.extractor import SourceKind
from obsidian_ingest.extractor.html_text import (
    Extraction,
    ExtractionBuilder,
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
    result = ExtractionBuilder()
    hints = sanitize(parse(markup), URL, result)
    _Walker(URL, hints, result).walk(etree.fromstring(f"<main>{xml}</main>"))
    return result.build()


def _envelope_text_size(text: str) -> int:
    return len(responses.compact_json(responses.compact_json(text)).encode())


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
    assert all(_envelope_text_size(row[-1]) <= 7_000 for row in rows)
    assert "".join(row[-1].removeprefix("(continued) ") for row in rows) == text


def test_html_atoms_count_json_escapes_and_utf8_bytes() -> None:
    text = 'ع"\\' * 4_500
    result = _render(f"<p>{text}</p>")
    assert len(result.atoms) > 1
    assert all(_envelope_text_size(atom.text) <= 7_000 for atom in result.atoms)


def test_escape_heavy_rows_page_in_full_with_a_token() -> None:
    label = '\\"' * 1_000
    text = '\\"\x01' * 4_000
    atoms, _ = markdown_atoms(f"## {label}\n\n{text}")
    source = _source(
        tuple(TranscriptSegment(a.text, kind=a.kind, section=a.section) for a in atoms)
    )
    rows = []
    offset, token = 0, None
    while offset is not None:
        page = responses.source_page(source, offset=offset, limit=1, read_token=token)
        assert page["read_token"]
        assert len(page["segments"]) == 1
        assert responses.fits_envelope(page, responses.MAX_PAGE_ENVELOPE_BYTES)
        rows.extend(page["segments"])
        offset, token = page["next_offset"], page["read_token"]
    assert [row[0] for row in rows] == list(range(len(atoms)))
    assert all(_envelope_text_size(row[1]) <= 1_500 for row in rows)
    assert all(_envelope_text_size(row[-1]) <= 7_000 for row in rows)
    assert "".join(row[-1].removeprefix("(continued) ") for row in rows) == text


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
    result = _render("<p>" + "x" * 6_993 + "[link](javascript:…)</p>")
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


def test_html_literal_heading_and_highlight_markers_are_escaped() -> None:
    result = _render("<p>  # Not a heading<lb/><lb/>==not highlighted==</p>")
    assert [atom.text for atom in result.atoms] == [r"\# Not a heading", r"\==not highlighted=="]


@pytest.mark.parametrize("repeated", [1, 2_000])
def test_code_rows_keep_fences_and_reconstruct_the_original(repeated: int) -> None:
    code = ('first "\\\n```\n  rest\n' * repeated).removesuffix("\n")
    html_code = _render(f"<code>{code}</code>").atoms
    clip_code, _ = markdown_atoms(f"~~~py\n{code}\n~~~")
    for atoms, language in ((html_code, ""), (clip_code, "py")):
        assert (len(atoms) > 1) == (repeated > 1)
        pieces = []
        for atom in atoms:
            opening, _, rest = atom.text.partition("\n")
            payload, _, closing = rest.rpartition("\n")
            assert atom.kind == "code"
            assert opening == closing + language
            assert len(closing) > max(map(len, re.findall("`+", payload)), default=0)
            assert _envelope_text_size(atom.text) <= 7_000
            pieces.append(payload)
        assert "".join(pieces) == code


@pytest.mark.parametrize(
    ("html_attributes", "body_attributes"), [(" hidden", ""), ("", ' style="display: none"')]
)
def test_hidden_document_or_body_has_no_text(html_attributes: str, body_attributes: str) -> None:
    markup = (
        f"<html{html_attributes}><body{body_attributes}><article><p>"
        + "A full paragraph of hidden text that should not be part of the article. " * 10
        + "</p></article></body></html>"
    )
    assert extract_html(markup, URL).atoms == ()


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


def test_caption_follows_its_figure_when_other_occurrences_share_the_url() -> None:
    markup = (
        '<img src="/image#original" alt="standalone">'
        '<p>Inline <img src="/image#original" alt="inline"> text</p>'
        '<figure><img src="/image#original" alt="figure one"><figcaption>One</figcaption></figure>'
        '<figure><img src="/image#original" alt="figure two"><figcaption>Two</figcaption></figure>'
    )
    result = ExtractionBuilder()
    document = parse(markup)
    hints = sanitize(document, URL, result)
    images = list(document.iter("img"))
    main = etree.Element("main")
    etree.SubElement(main, "graphic", **dict(images[0].attrib))
    paragraph = etree.SubElement(main, "p")
    etree.SubElement(paragraph, "graphic", **dict(images[1].attrib))
    etree.SubElement(main, "graphic", **dict(images[3].attrib))
    _Walker(URL, hints, result).walk(main)
    assert [(a.kind, a.text) for a in result.atoms] == [
        ("image", "![standalone](https://example.com/image#original)"),
        ("paragraph", "![inline](https://example.com/image#original)"),
        ("image", "![figure two](https://example.com/image#original)"),
        ("caption", "Two"),
    ]


def test_extraction_preserves_figure_marker_without_exposing_it_in_rows() -> None:
    prose = "This is a detailed paragraph of article text. " * 40
    result = extract_html(
        f"<article><h1>Title</h1><p>{prose}</p>"
        '<img src="/image.png" alt="standalone">'
        f'<p>Inline <img src="/image.png" alt="inline"> {prose}</p>'
        '<figure><img src="/image.png" alt="figure"><figcaption>Correct caption</figcaption></figure>'
        f"<p>{prose}</p></article>",
        URL,
    )
    rows = [(a.kind, a.text) for a in result.atoms if a.kind in {"image", "caption"}]
    assert rows == [
        ("image", "![standalone](https://example.com/image.png)"),
        ("image", "![figure](https://example.com/image.png)"),
        ("caption", "Correct caption"),
    ]
    assert any("![inline](https://example.com/image.png)" in a.text for a in result.atoms)


def test_long_parent_yields_space_to_the_leaf() -> None:
    result = _render(
        f'<head rend="h2">{"parent" * 200}</head><head rend="h3">Leaf label</head><p>Body</p>'
    )
    assert result.atoms[0].section.endswith("… › Leaf label")


def test_html_heading_paths_use_full_labels_and_remain_unique() -> None:
    result = _render(
        '<head rend="h2">Results (UK)</head><p>One</p>'
        '<head rend="h2">Results (UK)</head><p>Two</p>'
        '<head rend="h2">Results (UK) (2)</head><p>Three</p>'
        '<head rend="h3">Detail</head><p>Four</p>'
    )
    assert result.sections == (
        ("Results (UK)", 0),
        ("Results (UK) (2)", 1),
        ("Results (UK) (2) (2)", 2),
        ("Results (UK) (2) (2) › Detail", 3),
    )
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


def test_rich_article_reads_back_through_the_cli(tmp_path, monkeypatch, cli_runner) -> None:
    from articles import FakeWeb, Reply, page, words

    url = "https://example.com/rich"
    body = (
        "<article><h1>Rich</h1>"
        f"<p>Intro with <a href='/docs'>the docs</a>. {words(60, 'intro')}.</p>"
        "<figure><img src='/chart.png' alt='A chart'><figcaption>Figure 1</figcaption></figure>"
        "<h2>Code</h2><pre><code class='language-python'>print('hi')</code></pre>"
        f"<p>{words(40, 'mid')}.</p>"
        "<h2>Data</h2><table><tr><th>Name</th><th>Value</th></tr>"
        "<tr><td>alpha</td><td>1</td></tr></table>"
        f"<p>{words(40, 'end')}.</p></article>"
    )
    FakeWeb(routes={url: Reply(page(body, head="<title>Rich</title>"))}).install(monkeypatch)
    db = ("--cache-db", str(tmp_path / "sources.db"))
    source_id = cli_runner.ok("ingest-source", url, *db)["source_id"]
    rows = cli_runner.ok("read-source", source_id, *db)["segments"]
    by_kind = {kind: text for _, _, kind, text in rows}
    assert "Intro with [the docs](https://example.com/docs)." in rows[0][3]
    assert by_kind["image"] == "![A chart](https://example.com/chart.png)"
    assert by_kind["caption"] == "Figure 1"
    assert by_kind["code"] == "```python\nprint('hi')\n```"
    assert by_kind["table_row"] == "Name: alpha; Value: 1"
    sections = {section for _, section, _, _ in rows}
    assert {"Code", "Data"} <= sections


# --- Math and footnotes ---------------------------------------------------------------------


def _math_page(body: str) -> list[tuple[str, str | None, str]]:
    from articles import page, words

    markup = page(f"<article><h1>Maths</h1><p>{words(60, 'intro')}.</p>{body}</article>")
    return [(a.kind, a.section, a.text) for a in extract_html(markup, URL).atoms[1:]]


@pytest.mark.parametrize(
    ("math", "rendered"),
    [
        (
            (
                "<math><semantics><mi>e</mi>"
                "<annotation encoding='application/x-tex'>e^{i\\pi}</annotation></semantics></math>"
            ),
            "$e^{i\\pi}$",
        ),
        ("<math alttext='a^2 + b^2'><mi>a</mi></math>", "$a^2 + b^2$"),
        (
            (
                "<span class='katex'><span class='katex-mathml'><math><semantics><mi>x</mi>"
                "<annotation encoding='application/x-tex'>x^2</annotation></semantics></math>"
                "</span><span class='katex-html' aria-hidden='true'>x2</span></span>"
            ),
            "$x^2$",
        ),
        ("<script type='math/tex'>\\frac{1}{2}</script>", "$\\frac{1}{2}$"),
    ],
    ids=["annotation", "alttext", "katex", "mathjax-script"],
)
def test_inline_math_becomes_obsidian_math(math: str, rendered: str) -> None:
    rows = _math_page(f"<p>Before {math} after.</p>")
    assert rows == [("paragraph", None, f"Before {rendered} after.")]


def test_display_math_is_its_own_row() -> None:
    rows = _math_page(
        "<div><math display='block' alttext='\\int_0^1 f(x)\\,dx'><mi>f</mi></math></div>"
        "<script type='math/tex; mode=display'>E = mc^2</script>"
    )
    assert rows == [("math", None, "$$\\int_0^1 f(x)\\,dx$$"), ("math", None, "$$E = mc^2$$")]


def test_markdown_inside_tex_stays_inside_the_math() -> None:
    rows = _math_page("<p>See <math alttext='a$ [x](javascript:alert(1)) $b'></math> now.</p>")
    assert rows == [("paragraph", None, "See $a [x](javascript:alert(1)) b$ now.")]


def test_literal_text_outside_math_is_still_escaped() -> None:
    rows = _math_page("<p>A [link](javascript:x) and \\(a_b\\) here.</p>")
    assert rows == [("paragraph", None, "A \\[link\\](javascript:x) and $a_b$ here.")]


@pytest.mark.parametrize(
    "notes",
    [
        (
            "<section role='doc-endnotes'><h2>Notes</h2><ol>"
            "<li id='fn1'>First note.</li><li id='fn2'>Second note.</li></ol></section>"
        ),
        (
            "<div class='reflist'><ol class='references'>"
            "<li id='cite_note-1'><span class='mw-cite-backlink'>^</span> "
            "<span class='reference-text'>First note.</span></li>"
            "<li id='cite_note-2'><span class='reference-text'>Second note.</span></li></ol></div>"
        ),
    ],
    ids=["doc-endnotes", "wikipedia-reflist"],
)
def test_footnotes_get_their_own_section_and_markers(notes: str) -> None:
    rows = _math_page(f"<p>A claim.<sup><a href='#fn1'>[1]</a></sup></p>{notes}")
    assert rows[0][0] == "paragraph" and "\\[1\\]" in rows[0][2]
    footnotes = [(kind, section, text) for kind, section, text in rows if kind == "footnote"]
    assert [section for _, section, _ in footnotes] == ["Footnotes", "Footnotes"]
    assert footnotes[0][2].startswith("\\[1\\]") and "First note." in footnotes[0][2]
    assert footnotes[1][2].startswith("\\[2\\]") and "Second note." in footnotes[1][2]


def test_fragment_links_keep_the_page_path() -> None:
    from articles import words

    rows = _math_page(
        f"<p>A claim with context. {words(30, 'claim')}.<sup><a href='#fn1'>[1]</a></sup> "
        "See the <a href='#intro'>introduction above</a> for more.</p>"
    )
    assert "(https://example.com/post#fn1)" in rows[0][2]
    assert "[introduction above](https://example.com/post#intro)" in rows[0][2]


def test_hidden_math_stays_hidden() -> None:
    rows = _math_page(
        "<p>Shown <span hidden><math alttext='secret'><mi>s</mi></math></span> text "
        "<math alttext='x' style='display:none'><mi>x</mi></math> end.</p>"
        "<div aria-hidden='true'><math display='block' alttext='hidden'></math></div>"
    )
    assert all("secret" not in text and "hidden" not in text for *_, text in rows)


def test_a_split_row_writes_its_formulas_as_escaped_text() -> None:
    from articles import words

    long_text = (words(900, "longword") + ". ") * 2
    rows = _math_page(f"<p>{long_text} Then <math alttext='a [x](javascript:y) b'></math>.</p>")
    assert len(rows) > 1
    joined = "".join(text for *_, text in rows)
    assert "$" not in joined
    # Shown as the literal text \(a [x](javascript:y) b\), with every mark escaped.
    assert "\\\\(a \\[x\\](javascript:y) b\\\\)" in joined


def test_page_text_cannot_forge_math_markers() -> None:
    rows = _math_page("<p>Text $[x](javascript:y) more.</p>")
    assert rows == [("paragraph", None, "Text $\\[x\\](javascript:y) more.")]


def test_a_second_footnote_list_keeps_the_footnote_kind() -> None:
    notes = "<section class='footnotes'><ol><li>One.</li></ol></section>"
    rows = _math_page(f"<p>Body text.</p>{notes}<h2>More</h2><p>Later text.</p>{notes}")
    footnotes = [(section, text) for kind, section, text in rows if kind == "footnote"]
    assert footnotes == [("Footnotes", "\\[1\\] One."), ("Footnotes (2)", "\\[1\\] One.")]
