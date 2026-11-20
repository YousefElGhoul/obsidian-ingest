"""HTML to text atoms: sanitize, extract the main content, render Markdown atoms.

1. Sanitize (lxml): remove everything a reader cannot see (scripts, styles, templates,
   comments, hidden and aria-hidden elements, form controls) so hidden instructions
   never reach the agent, and prune page chrome (ads, sponsored and promo blocks,
   newsletter and paywall prompts, related-story and share widgets, cookie banners,
   comment sections) with a small, named list of class/id tokens.
2. Extract (trafilatura): main text as XML, keeping headings, links, images, lists,
   quotes, code, tables and inline formatting.
3. Render: one atom per block in source order, as Markdown, with the heading path as
   its section. Only http(s) links and images are kept as links; others become text.
   Figure captions and code languages, which the extractor drops, are restored from
   the sanitized HTML.
"""

import re
import unicodedata
import warnings
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from lxml import etree, html

from obsidian_ingest.extractor.quality import instruction_like

SECTION_SEPARATOR = " › "
MAX_ATOM_CHARS = 6000
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
_IMAGE_ONLY = re.compile(r"(\s*!\[[^\]]*\]\([^)]*\)\s*)+")
_SPACER = re.compile(r"(spacer|1x1|pixel|blank|trans(parent)?(_1x1)?)\.(gif|png)$", re.IGNORECASE)
_BLOCKS = {"p", "head", "list", "item", "quote", "table", "row"}
_INLINE_MARKS = {"#b": "**", "#i": "*", "#u": "", "#t": "`", "#s": "~~"}


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
    """Remove invisible content and page chrome in place; return recovered hints."""
    hints: dict = {"captions": {}, "code_languages": {}}
    for node in list(document.iter(etree.Comment, etree.ProcessingInstruction)):
        _drop(node)
        result.removed_hidden += 1
    for element in list(document.iter(*_REMOVED_TAGS)):
        _drop(element)
        result.removed_hidden += 1
    for element in list(document.iter()):
        if not isinstance(element.tag, str) or element.getparent() is None:
            continue
        hidden = (
            element.get("hidden") is not None
            or element.get("aria-hidden", "").lower() == "true"
            or _HIDDEN_STYLE.search(element.get("style", ""))
        )
        if hidden:
            _drop(element)
            result.removed_hidden += 1
        elif (
            element.tag not in _PROTECTED
            and _tokens(element) & CHROME_TOKENS
            and len(element.text_content().split()) <= CHROME_MAX_WORDS
        ):
            _drop(element)
            result.removed_chrome += 1
    for figure in document.iter("figure"):
        image = next(figure.iter("img"), None)
        caption = next(figure.iter("figcaption"), None)
        if image is not None and caption is not None and image.get("src"):
            text = " ".join(clean_text(caption.text_content()).split())
            if text:
                hints["captions"][urljoin(base_url, image.get("src"))] = text
    for code in document.iter("code"):
        match = re.search(r"(?:language|lang)-([A-Za-z0-9_+-]+)", code.get("class", ""))
        if match and code.getparent() is not None and code.getparent().tag == "pre":
            hints["code_languages"][_code_key(code.text_content())] = match.group(1)
    return hints


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
    absolute = urljoin(base_url, value.strip())
    return absolute if urlsplit(absolute).scheme in {"http", "https"} else None


def _inline(element: etree._Element, base_url: str, result: Extraction) -> str:
    """Render an element's mixed content as one line of Markdown."""
    parts = [element.text or ""]
    for child in element:
        inner = _inline(child, base_url, result)
        tag = child.tag
        if tag == "ref":
            target = _safe_url(child.get("target"), base_url)
            if target and inner.strip():
                result.links += 1
                result.link_words += len(inner.split())
                inner = f"[{inner}]({target})"
        elif tag == "hi":
            rend = child.get("rend", "")
            if rend in {"#sub", "#sup"}:
                inner = f"<{rend[1:]}>{inner}</{rend[1:]}>"
            elif _INLINE_MARKS.get(rend) and inner.strip():
                mark = _INLINE_MARKS[rend]
                inner = f"{mark}{inner}{mark}"
        elif tag == "code":
            inner = f"`{inner}`"
        elif tag == "lb":
            inner = "\n"
        elif tag == "graphic":
            inner = _image(child, base_url, result) or ""
        elif tag in _BLOCKS:
            # A block nested in inline context (paragraphs inside a table cell).
            inner = f"\n\n{inner}\n\n"
        parts.append(inner)
        parts.append(child.tail or "")
    return "".join(parts)


def _image(element: etree._Element, base_url: str, result: Extraction) -> str | None:
    src = _safe_url(element.get("src"), base_url)
    if src is None or _SPACER.search(urlsplit(src).path):
        return None
    result.images += 1
    alt = " ".join(clean_text(element.get("alt") or "").split()).replace("]", ")")
    if instruction_like([alt]):
        alt = ""  # Alt text is not shown to readers; instructions there are hidden text.
    return f"![{alt}]({src})"


def _line(text: str) -> str:
    return " ".join(clean_text(text).split())


def _paragraphs(text: str) -> list[str]:
    """Split on blank-line breaks: some pages separate paragraphs with <br><br>."""
    return [line for line in (_line(part) for part in re.split(r"\n[ \t]*\n", text)) if line]


def _split(text: str) -> list[str]:
    """Split an oversized block at line or sentence boundaries; never truncate."""
    if len(text) <= MAX_ATOM_CHARS:
        return [text]
    parts = []
    while len(text) > MAX_ATOM_CHARS:
        window = text[:MAX_ATOM_CHARS]
        cut = max(window.rfind("\n"), window.rfind(". ") + 1)
        cut = cut if cut > MAX_ATOM_CHARS // 2 else MAX_ATOM_CHARS
        parts.append(text[:cut].rstrip())
        text = CONTINUED + text[cut:].lstrip()
    parts.append(text)
    return parts


class _Walker:
    def __init__(self, base_url: str, hints: dict, result: Extraction) -> None:
        self.base_url = base_url
        self.hints = hints
        self.result = result
        self.headings: list[tuple[int, str]] = []
        self.title: str | None = None

    def section(self) -> str | None:
        return SECTION_SEPARATOR.join(text for _, text in self.headings) or None

    def emit(self, text: str, kind: str) -> None:
        # Leading spaces carry list nesting; everything else is trimmed.
        text = text.rstrip() if kind == "list_item" else text.strip()
        if not text.strip():
            return
        self.result.words += len(text.split())
        for part in _split(text):
            self.result.atoms.append(Atom(part, kind, self.section()))

    def heading(self, element: etree._Element) -> None:
        level = int((element.get("rend") or "h2").lstrip("h") or 2)
        text = _line(_inline(element, self.base_url, self.result))
        if not text:
            return
        # A leading h1 repeating the title is the article's name, not a section.
        if level == 1 and not self.result.atoms and self.title and text == self.title:
            return
        self.headings = [(lvl, t) for lvl, t in self.headings if lvl < level]
        self.headings.append((level, text))
        path = self.section() or text
        # Repeated headings get an ordinal so section labels stay unique and stable.
        used = sum(1 for existing, _ in self.result.sections if existing.split(" (")[0] == path)
        if used:
            self.headings[-1] = (level, f"{text} ({used + 1})")
            path = self.section() or text
        self.result.sections.append((path, len(self.result.atoms)))

    def walk(self, element: etree._Element) -> None:
        for child in element:
            tag = child.tag
            if tag == "head":
                self.heading(child)
            elif tag == "p":
                for paragraph in _paragraphs(_inline(child, self.base_url, self.result)):
                    self.emit(paragraph, "paragraph")
            elif tag == "quote":
                self.emit(_line(_inline(child, self.base_url, self.result)), "quote")
            elif tag == "list":
                self.list(child, 0)
            elif tag == "code":
                code = clean_text("".join(child.itertext())).strip("\n")
                language = self.hints["code_languages"].get(_code_key(code), "")
                self.emit(f"```{language}\n{code}\n```", "code")
            elif tag == "table":
                self.table(child)
            elif tag == "graphic":
                image = _image(child, self.base_url, self.result)
                if image:
                    self.emit(image, "image")
                    absolute = _safe_url(child.get("src"), self.base_url)
                    caption = self.hints["captions"].get(absolute or "")
                    if caption:
                        self.emit(caption, "caption")
            else:
                self.walk(child)

    def list(self, element: etree._Element, depth: int) -> None:
        for item in element.iter("item"):
            if item.getparent() is not element:
                continue
            nested = [child for child in item if child.tag == "list"]
            for child in nested:
                item.remove(child)
            text = _line(_inline(item, self.base_url, self.result))
            self.emit(("  " * depth) + text if text else "", "list_item")
            for child in nested:
                self.list(child, depth + 1)

    def table(self, element: etree._Element) -> None:
        raw_rows = [
            [_inline(cell, self.base_url, self.result) for cell in row.iter("cell")]
            for row in element.iter("row")
        ]
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


def extract_html(markup: str, base_url: str) -> Extraction:
    """Sanitize and extract one HTML document into Markdown atoms."""
    with warnings.catch_warnings():
        # Imported lazily (heavy, only text sources need it); its urllib3 import warns
        # about optional SOCKS support on stderr, which MCP hosts show as noise.
        warnings.simplefilter("ignore")
        import trafilatura

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
