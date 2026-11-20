"""HTML to text atoms: sanitize, extract the main content, render Markdown atoms.

1. Sanitize (lxml): remove scripts, styles, templates, comments, explicitly hidden
   elements and form controls, and prune page chrome (ads, sponsored and promo blocks,
   newsletter and paywall prompts, related-story and share widgets, cookie banners,
   comment sections) with a small, named list of class/id tokens. Stylesheet visibility
   is not evaluated; pruning and instruction detection are heuristics, not guarantees.
   Retained text, including image alt text, is untrusted.
2. Extract (trafilatura): main text as XML, keeping headings, links, images, lists,
   quotes, code, tables and inline formatting.
3. Render: one atom per block in source order, as Markdown, with the heading path as
   its section. Only http(s) links and images are kept as links; others become text.
   Figure captions and code languages, which the extractor drops, are restored from
   the sanitized HTML.
"""

import json
import re
import unicodedata
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote, urljoin, urlsplit

from lxml import etree, html

from obsidian_ingest.extractor.quality import instruction_like

SECTION_SEPARATOR = " › "
MAX_ATOM_BYTES = 7_000
MAX_SECTION_BYTES = 1_500
MAX_SECTION_CHARS = 500
LAYOUT_CELL_WORDS = 150
CONTINUED = "(continued) "
# Bidi embedding/override/isolate controls and zero-width characters (outside the
# joiners scripts need) can hide or reorder text; natural RTL text needs none of them.
_INVISIBLE = re.compile("[\u202a-\u202e\u2066-\u2069\u200b\u2060\ufeff]")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
# Page chrome, matched as whole class/id tokens so "header", "download" or "ad-layout"
# never match. Blocks longer than CHROME_MAX_WORDS are content, whatever their names.
CHROME_TOKENS = frozenset(
    {
        "ad", "ads", "advert", "advertisement", "adslot", "ad-slot", "ad-container",
        "ad-wrapper", "ad-unit", "ad-banner", "sponsored", "promo", "newsletter",
        "newsletter-signup", "subscribe", "signup", "paywall", "related", "related-posts",
        "related-articles", "related-stories", "recommended", "share", "sharing",
        "share-buttons", "social-share", "social", "cookie", "cookies", "cookie-banner",
        "consent", "comments", "comment-section", "comments-section", "disqus",
        "outbrain", "taboola",
    }
)  # fmt: skip
CHROME_MAX_WORDS = 300
_PROTECTED = {"html", "body", "article", "main"}
_REMOVED_TAGS = ("script", "style", "noscript", "template", "iframe", "object", "embed",
                 "form", "input", "textarea", "select", "button", "svg", "canvas")  # fmt: skip
_IMAGE_ONLY = re.compile(r"(\s*!\[(?:\\.|[^\]\\])*\]\([^)]*\)\s*)+")
_SPACER = re.compile(r"(spacer|1x1|pixel|blank|trans(parent)?(_1x1)?)\.(gif|png)$", re.IGNORECASE)
_BLOCKS = {"p", "head", "list", "item", "quote", "table", "row"}
_INLINE_MARKS = {"#b": "**", "#i": "*", "#s": "~~"}
_LITERAL_MARKS = re.compile(r"([\\`*_\[\]<>|]|%(?=%)|(?<=%)%)")
_DESTINATION_ESCAPES = str.maketrans({char: quote(char, safe="") for char in "()<> "})
_SENTENCE_END = re.compile(r"[.!?؟。！？](?=\s)")
_ROW_MARKER = re.compile(r"(?m)^([ \t]*)(#|={2,})")
_FENCE = re.compile(r"^(`{3,})([^`\r\n]*)$|^(~{3,})(.*)$")


@dataclass(frozen=True, slots=True)
class Atom:
    """One rendered block: Markdown text, its kind, and its heading path."""

    text: str
    kind: str
    section: str | None


@dataclass(slots=True)
class Extraction:
    """Atoms plus what the extraction kept and removed, for evidence and quality."""

    atoms: list[Atom] = field(default_factory=list)
    sections: list[tuple[str, int]] = field(default_factory=list)  # (path, first atom)
    doc: dict[str, str] = field(default_factory=dict)  # trafilatura's metadata attributes
    removed_hidden: int = 0
    removed_chrome: int = 0
    links: int = 0
    images: int = 0
    words: int = 0
    link_words: int = 0


class SectionPaths:
    """Allocate distinct heading paths while bounding section size in each row."""

    def __init__(self) -> None:
        self._headings: list[tuple[int, str]] = []
        self._counts: dict[str, int] = {}
        self._used: set[str] = set()

    @property
    def current(self) -> str | None:
        return self._headings[-1][1] if self._headings else None

    def add(self, level: int, label: str) -> str:
        while self._headings and self._headings[-1][0] >= level:
            self._headings.pop()
        parent = self.current
        base = _section_path(parent, label)
        count = self._counts.get(base, 0) + 1
        path = base
        while count > 1 or path in self._used:
            count = max(count, 2)
            path = _section_path(parent, label, f" ({count})")
            if path not in self._used:
                break
            count += 1
        self._counts[base] = count
        self._used.add(path)
        self._headings.append((level, path))
        return path


def _section_path(parent: str | None, leaf: str, suffix: str = "") -> str:
    def fits(path: str) -> bool:
        return len(path) <= MAX_SECTION_CHARS and _envelope_text_size(path) <= MAX_SECTION_BYTES

    tail = leaf + suffix
    if not fits(tail):
        end = _fitting_end(leaf, 0, lambda part: fits(part + "…" + suffix), MAX_SECTION_CHARS)
        return leaf[:end] + "…" + suffix
    if parent:
        if fits(parent + SECTION_SEPARATOR + tail):
            return parent + SECTION_SEPARATOR + tail
        ellipsis = "…" + SECTION_SEPARATOR + tail
        if fits(ellipsis):
            end = _fitting_end(parent, 0, lambda part: fits(part + ellipsis), MAX_SECTION_CHARS)
            return parent[:end].rstrip() + ellipsis
    return tail


def clean_text(text: str) -> str:
    """NFC-normalize and strip invisible controls; keep line structure."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFC", text))


def _tokens(element: etree._Element) -> set[str]:
    raw = f"{element.get('class', '')} {element.get('id', '')}".lower()
    return set(re.split(r"[^a-z0-9_-]+", raw))


def parse(markup: str) -> etree._Element:
    """Parse HTML text into a tree, tolerating an XML declaration."""
    markup = re.sub(r"^\s*<\?xml[^>]*\?>", "", markup)
    return html.document_fromstring(markup or "<html></html>")


def sanitize(document: etree._Element, base_url: str, result: Extraction) -> dict:
    """Remove explicitly hidden content and page chrome; return recovered hints."""
    captions: dict[str, tuple[str, str]] = {}
    code_languages: dict[str, str] = {}
    for node in list(document.iter(etree.Comment, etree.ProcessingInstruction)):
        _drop(node)
        result.removed_hidden += 1
    for element in list(document.iter(*_REMOVED_TAGS)):
        _drop(element)
        result.removed_hidden += 1
    for element in list(document.iter()):
        if not isinstance(element.tag, str) or (
            element is not document and element.getparent() is None
        ):
            continue
        hidden = (
            element.get("hidden") is not None
            or element.get("aria-hidden", "").lower() == "true"
            or _HIDDEN_STYLE.search(element.get("style", ""))
        )
        if hidden:
            result.removed_hidden += 1
            if element is document:
                document.clear()
                break
            _drop(element)
        elif (
            element.tag not in _PROTECTED
            and _tokens(element) & CHROME_TOKENS
            and len(element.text_content().split()) <= CHROME_MAX_WORDS
        ):
            _drop(element)
            result.removed_chrome += 1
    image_urls = {
        url for image in document.iter("img") if (url := _safe_url(image.get("src"), base_url))
    }
    occurrence = 0
    for figure in document.iter("figure"):
        image = next(figure.iter("img"), None)
        caption = next(figure.iter("figcaption"), None)
        if (
            image is not None
            and caption is not None
            and (url := _safe_url(image.get("src"), base_url))
            and url not in captions
            and (text := _line(caption.text_content()))
        ):
            while True:
                occurrence += 1
                marker = f"{url.split('#', 1)[0]}#oi-caption-{occurrence}"
                if marker not in image_urls:
                    break
            image.set("src", marker)
            captions[marker] = (url, text)
    for code in document.iter("code"):
        match = re.search(r"(?:language|lang)-([A-Za-z0-9_+-]+)", code.get("class", ""))
        if match and code.getparent() is not None and code.getparent().tag == "pre":
            code_languages[_code_key(code.text_content())] = match.group(1)
    return {"captions": captions, "code_languages": code_languages}


def _drop(node: etree._Element) -> None:
    """Remove a node but keep its tail text, which belongs to the parent."""
    parent = node.getparent()
    if parent is None:
        return
    if node.tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def _code_key(text: str) -> str:
    return " ".join(text.split())[:500]


def _safe_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    try:
        absolute = urljoin(base_url, value.strip())
        return absolute if urlsplit(absolute).scheme in {"http", "https"} else None
    except ValueError:
        return None


def escape_destination(url: str) -> str:
    return url.translate(_DESTINATION_ESCAPES)


def _literal(text: str | None) -> str:
    return _LITERAL_MARKS.sub(r"\\\1", text or "")


def escape_row_markers(text: str) -> str:
    return _ROW_MARKER.sub(lambda match: match[1] + re.sub(r"#|={1,2}", r"\\\g<0>", match[2]), text)


def _line(text: str) -> str:
    return " ".join(clean_text(text).split())


def _paragraphs(text: str) -> list[str]:
    """Split on blank-line breaks: some pages separate paragraphs with <br><br>."""
    return [line for line in (_line(part) for part in re.split(r"\n[ \t]*\n", text)) if line]


def split_atom(text: str) -> list[str]:
    """Split on a line, a sentence, or a character without dropping content."""
    parts: list[str] = []
    start = 0
    prefix = ""
    while True:
        end = _fitting_end(
            text, start, lambda part, prefix=prefix: _atom_fits(prefix + part), MAX_ATOM_BYTES
        )
        if end == len(text):
            parts.append(prefix + text[start:end])
            return parts
        window = text[start:end]
        if (newline := window.rfind("\n")) >= 0:
            cut = newline + 1
        else:
            cut = max(
                (match.end() for match in _SENTENCE_END.finditer(window)), default=len(window)
            )
            # Keep a Markdown escape with the character it protects.
            if (cut - len(window[:cut].rstrip("\\"))) % 2:
                cut -= 1
        parts.append(prefix + window[:cut])
        start += cut
        prefix = CONTINUED


def _atom_fits(text: str) -> bool:
    return _envelope_text_size(text) <= MAX_ATOM_BYTES


def _envelope_text_size(text: str) -> int:
    # The page JSON is itself a string inside the MCP text-content envelope.
    return len(json.dumps(json.dumps(text, ensure_ascii=False), ensure_ascii=False).encode())


def _fitting_end(text: str, start: int, fits: Callable[[str], bool], limit: int) -> int:
    end = min(len(text), start + limit)
    if fits(text[start:end]):
        return end
    low = start
    while low < end:
        middle = (low + end + 1) // 2
        if fits(text[start:middle]):
            low = middle
        else:
            end = middle - 1
    return end


def _backtick_run(text: str) -> int:
    return max((len(match[0]) for match in re.finditer(r"`+", text)), default=0)


def open_fence(line: str) -> tuple[str, str] | None:
    if match := _FENCE.fullmatch(line):
        return (match[1], match[2]) if match[1] else (match[3], match[4])
    return None


def closes_fence(line: str, marker: str) -> bool:
    stripped = line.rstrip(" \t\r\n")
    return stripped.startswith(marker) and not stripped.strip(marker[0])


def code_atoms(code: str, language: str) -> list[str]:
    language = language[:64]  # An untrusted info string must leave room for code.

    def fenced(part: str) -> str:
        marker = "`" * max(3, _backtick_run(part) + 1)
        return f"{marker}{language}\n{part}\n{marker}"

    parts: list[str] = []
    start = 0
    while True:
        end = _fitting_end(code, start, lambda part: _atom_fits(fenced(part)), MAX_ATOM_BYTES)
        if end < len(code) and (newline := code.rfind("\n", start, end)) >= start:
            end = newline + 1
        parts.append(fenced(code[start:end]))
        if end == len(code):
            return parts
        start = end


class _Walker:
    def __init__(self, base_url: str, hints: dict, result: Extraction) -> None:
        self.base_url = base_url
        self.hints = hints
        self.result = result
        self.headings = SectionPaths()
        self.title: str | None = None

    def emit(self, text: str, kind: str) -> None:
        # Leading spaces carry list nesting; everything else is trimmed.
        text = text.rstrip() if kind == "list_item" else text.strip()
        if not text.strip():
            return
        self.result.words += len(text.split())
        for part in split_atom(escape_row_markers(text)):
            self.result.atoms.append(Atom(part, kind, self.headings.current))

    def inline(self, element: etree._Element) -> str:
        parts = [_literal(element.text)]
        for child in element:
            parts.extend((self.inline_child(child), _literal(child.tail)))
        return "".join(parts)

    def inline_child(self, child: etree._Element) -> str:
        tag = child.tag
        rend = child.get("rend", "")
        if tag == "code" or (tag == "hi" and rend == "#t"):
            code = "".join(child.itertext())
            marker = "`" * (_backtick_run(code) + 1)
            pad = " " if code.startswith("`") or code.endswith("`") else ""
            return f"{marker}{pad}{code}{pad}{marker}" if code.strip() else code
        if tag == "lb":
            return "\n"
        if tag == "graphic":
            image, caption = self.image(child)
            return f"{image}\n\n{caption}\n\n" if caption else image
        inner = self.inline(child)
        if tag == "ref":
            target = _safe_url(child.get("target"), self.base_url)
            if target and inner.strip():
                self.result.links += 1
                self.result.link_words += len(inner.split())
                return f"[{inner}]({escape_destination(target)})"
        elif tag == "hi":
            if rend in {"#sub", "#sup"}:
                return f"<{rend[1:]}>{inner}</{rend[1:]}>"
            if (mark := _INLINE_MARKS.get(rend)) and inner.strip():
                return f"{mark}{inner}{mark}"
        elif tag in _BLOCKS:
            return f"\n\n{inner}\n\n"
        return inner

    def image(self, element: etree._Element) -> tuple[str, str]:
        src = _safe_url(element.get("src"), self.base_url)
        if src is None or _SPACER.search(urlsplit(src).path):
            return "", ""
        src, caption = self.hints["captions"].get(src, (src, ""))
        self.result.images += 1
        alt = _line(element.get("alt") or "")
        if instruction_like([alt]):
            alt = ""
        return f"![{_literal(alt)}]({escape_destination(src)})", _literal(caption)

    def heading(self, element: etree._Element) -> None:
        level = int((element.get("rend") or "h2").lstrip("h") or 2)
        # Section paths are plain labels; the draft writer escapes them when citing.
        text = _line("".join(element.itertext()))
        if not text:
            return
        # A leading h1 repeating the title is the article's name, not a section.
        if level == 1 and not self.result.atoms and self.title and text == self.title:
            return
        self.result.sections.append((self.headings.add(level, text), len(self.result.atoms)))

    def walk(self, element: etree._Element) -> None:
        for child in element:
            tag = child.tag
            if tag == "head":
                self.heading(child)
            elif tag == "p":
                for paragraph in _paragraphs(self.inline(child)):
                    self.emit(paragraph, "paragraph")
            elif tag == "quote":
                self.emit(_line(self.inline(child)), "quote")
            elif tag == "list":
                self.list(child, 0)
            elif tag == "code":
                code = clean_text("".join(child.itertext())).strip("\n")
                language = self.hints["code_languages"].get(_code_key(code), "")
                self.result.words += len(code.split())
                self.result.atoms.extend(
                    Atom(part, "code", self.headings.current) for part in code_atoms(code, language)
                )
            elif tag == "table":
                self.table(child)
            elif tag == "graphic":
                image, caption = self.image(child)
                if image:
                    self.emit(image, "image")
                    self.emit(caption, "caption")
            else:
                self.walk(child)

    def list(self, element: etree._Element, depth: int) -> None:
        indent = "  " * depth
        for item in element.iterchildren("item"):
            parts = [_literal(item.text)]
            suffix = ""
            for child in item:
                if child.tag == "list":
                    if text := _line("".join(parts)):
                        self.emit(indent + text + suffix, "list_item")
                    self.list(child, depth + 1)
                    parts.clear()
                    suffix = " (continued)"
                else:
                    parts.append(self.inline_child(child))
                parts.append(_literal(child.tail))
            if text := _line("".join(parts)):
                self.emit(indent + text + suffix, "list_item")

    def table(self, element: etree._Element) -> None:
        raw_rows = [[self.inline(cell) for cell in row.iter("cell")] for row in element.iter("row")]
        # A cell holding prose (an essay laid out with tables) is text, not tabular data.
        if any(len(cell.split()) > LAYOUT_CELL_WORDS for row in raw_rows for cell in row):
            for row in raw_rows:
                for cell in row:
                    for paragraph in _paragraphs(cell):
                        kind = "image" if _IMAGE_ONLY.fullmatch(paragraph) else "paragraph"
                        self.emit(paragraph, kind)
            return
        heads = [row.find("cell[@role='head']") is not None for row in element.iter("row")]
        header: list[str] = []
        for raw, is_head in zip(raw_rows, heads, strict=True):
            cells = [_line(cell) for cell in raw]
            if is_head and not header:
                header = cells
                continue
            # Layout tables hold only images (spacers, logos); they are not content.
            if all(not cell or _IMAGE_ONLY.fullmatch(cell) for cell in cells):
                continue
            if header and len(header) == len(cells):
                text = "; ".join(f"{name}: {value}" for name, value in zip(header, cells))
            else:
                text = " | ".join(cells)
            self.emit(text, "table_row")


def load_trafilatura():
    """Import trafilatura lazily (heavy; only text sources need it), silencing the
    urllib3 SOCKS warning its import prints, which MCP hosts show as stderr noise."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import trafilatura
    return trafilatura


def extract_html(markup: str, base_url: str) -> Extraction:
    """Sanitize and extract one HTML document into Markdown atoms."""
    trafilatura = load_trafilatura()

    result = Extraction()
    document = parse(markup)
    hints = sanitize(document, base_url, result)
    cleaned = html.tostring(document, encoding="unicode")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        xml = trafilatura.extract(
            cleaned,
            url=base_url,
            output_format="xml",
            with_metadata=True,
            include_comments=False,
            include_tables=True,
            include_links=True,
            include_images=True,
            include_formatting=True,
            favor_precision=True,
        )
    if not xml:
        return result
    root = etree.fromstring(xml.encode("utf-8"), etree.XMLParser(resolve_entities=False))
    result.doc = {key: value for key, value in root.attrib.items() if value}
    walker = _Walker(base_url, hints, result)
    walker.title = _line(result.doc.get("title", "")) or None
    main = root.find("main")
    if main is not None:
        walker.walk(main)
    return result
