"""Web Clipper notes: a page the user saved with their own browser, as Markdown.

Obsidian Web Clipper writes YAML frontmatter (``title``, ``source``, ``author``,
``published``, ``created``...) followed by the page as Markdown. Only the fields this
tool uses are read, with a deliberately small parser: scalar values, quoted values and
simple lists. The body becomes the same atom kinds as fetched HTML, so links, images,
captions and formatting the clipper kept are preserved. The note is never modified.
"""

import re
from urllib.parse import urlsplit

from obsidian_ingest.extractor.html_text import (
    Atom,
    SectionPaths,
    clean_text,
    escape_destination,
    split_atom,
)
from obsidian_ingest.notes import ClippedNote  # noqa: F401  (re-exported for providers)

MAX_NOTE_CHARACTERS = 2_000_000
_FIELDS = {"title", "source", "url", "author", "authors", "published", "created", "lang",
           "language", "description", "site"}  # fmt: skip
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+)$")
_CLOSING_HASHES = re.compile(r"[ \t]+#+$")
_FENCE = re.compile(r"^(```+|~~~+)\s*([\w+-]*)")
_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])[ \t]+(.*)$")
_IMAGE_LINE = re.compile(r"^!\[(?:\\.|[^\]\\])*\]\([^)]*\)$")
_WIKILINK = re.compile(r"^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$")
_MARKDOWN_INLINE = re.compile(r"\\.|`+|!?\[|\]")
_LINK_OPEN = re.compile(r"\([ \t]*")
_BARE_DESTINATION = re.compile(r"\\.|[()]|\s")
_LINK_END = re.compile(r"""(?:[ \t]+(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\([^()]*\)))?[ \t]*\)""")


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    match = _WIKILINK.match(value)
    if match:
        value = match.group(2) or match.group(1)
    return " ".join(clean_text(value).split())


def split_frontmatter(text: str) -> tuple[dict[str, str | list[str]], str]:
    """Return the fields this tool reads, and the body after the frontmatter."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text
    fields: dict[str, str | list[str]] = {}
    current: str | None = None
    for line in lines[1:end]:
        item = re.match(r"^[ \t]*-[ \t]+(.*)$", line)
        if item and current is not None:
            value = _scalar(item.group(1))
            existing = fields.get(current)
            fields[current] = [*(existing if isinstance(existing, list) else []), value]
            continue
        pair = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not pair:
            current = None
            continue
        key = pair.group(1).lower()
        current = key if key in _FIELDS else None
        if current is None:
            continue
        raw = pair.group(2).strip()
        if raw.startswith("[") and raw.endswith("]") and not raw.startswith("[["):
            fields[key] = [_scalar(part) for part in raw[1:-1].split(",") if part.strip()]
        elif raw:
            fields[key] = _scalar(raw)
    return fields, "\n".join(lines[end + 1 :])


def _link_destination(text: str, start: int) -> tuple[str, int, int] | None:
    if text.startswith("<", start):
        end = text.find(">", start + 1)
        if end < 0:
            return None
        url, end = text[start + 1 : end], end + 1
    else:
        depth = 0
        end = len(text)
        for token in _BARE_DESTINATION.finditer(text, start):
            mark = token.group()
            if mark.isspace() or (mark == ")" and depth == 0):
                end = token.start()
                break
            depth += (mark == "(") - (mark == ")")
        if depth:
            return None
        url = text[start:end]
    closing = _LINK_END.match(text, end)
    return (url, end, closing.end()) if closing else None


def _safe_links(text: str) -> str:
    """Rewrite inline links and images, leaving code spans untouched."""
    parts: list[str] = []
    labels: list[tuple[int, str]] = []
    start = cursor = 0
    while match := _MARKDOWN_INLINE.search(text, cursor):
        cursor = match.end()
        token = match.group()
        if token.startswith("`"):
            closing = re.compile(rf"(?<!`){token}(?!`)").search(text, cursor)
            if closing:
                cursor = closing.end()
            continue
        if token in {"[", "!["}:
            parts.append(text[start : match.start()])
            labels.append((len(parts), token))
            parts.append(token)
            start = cursor
        if token != "]" or not labels:
            continue
        label_start, opening = labels.pop()
        if not (link := _LINK_OPEN.match(text, cursor)) or not (
            destination := _link_destination(text, link.end())
        ):
            continue
        url, tail, cursor = destination
        label = "".join(parts[label_start + 1 :]) + text[start : match.start()]
        try:
            parsed = urlsplit(url)
            allowed = parsed.scheme in {"http", "https"} and parsed.hostname
        except ValueError:
            allowed = False
        del parts[label_start:]
        if allowed:
            label = f"{opening}{label}]({escape_destination(url)}{text[tail:cursor]}"
        parts.append(label)
        start = cursor
    parts.append(text[start:])
    return "".join(parts)


def markdown_atoms(body: str) -> tuple[list[Atom], list[tuple[str, int]]]:
    """Split clipped Markdown into atoms and (section path, first atom) pairs."""
    atoms: list[Atom] = []
    sections: list[tuple[str, int]] = []
    headings = SectionPaths()
    paragraph: list[str] = []

    def emit(text: str, kind: str) -> None:
        # Leading spaces carry list nesting; everything else is trimmed.
        text = text.rstrip() if kind == "list_item" else text.strip()
        if text.strip():
            atoms.extend(Atom(part, kind, headings.current) for part in split_atom(text))

    def flush() -> None:
        if paragraph:
            text = _safe_links(" ".join(" ".join(line.split()) for line in paragraph))
            emit(text, "image" if _IMAGE_LINE.fullmatch(text) else "paragraph")
            paragraph.clear()

    lines = clean_text(body).splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        fence = _FENCE.match(stripped)
        if fence:
            flush()
            marker, language = fence.group(1), fence.group(2)
            code = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(marker):
                code.append(lines[index])
                index += 1
            emit(f"```{language}\n" + "\n".join(code) + "\n```", "code")
        elif heading := _HEADING.match(stripped):
            flush()
            level = len(heading.group(1))
            text = _safe_links(" ".join(_CLOSING_HASHES.sub("", heading.group(2)).split()))
            if text:
                sections.append((headings.add(level, text), len(atoms)))
        elif not stripped:
            flush()
        elif stripped.startswith(">"):
            flush()
            quote = [stripped.lstrip("> ").strip()]
            while index + 1 < len(lines) and lines[index + 1].strip().startswith(">"):
                index += 1
                quote.append(lines[index].strip().lstrip("> ").strip())
            emit(_safe_links(" ".join(part for part in quote if part)), "quote")
        elif stripped.startswith("|"):
            flush()
            rows = [stripped]
            while index + 1 < len(lines) and lines[index + 1].strip().startswith("|"):
                index += 1
                rows.append(lines[index].strip())
            cells = [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]
            body_rows = [c for c in cells if not all(re.fullmatch(r":?-+:?", x) for x in c if x)]
            header, data = (body_rows[0], body_rows[1:]) if len(body_rows) > 1 else ([], body_rows)
            for row in data:
                text = (
                    "; ".join(f"{name}: {value}" for name, value in zip(header, row))
                    if len(header) == len(row)
                    else " | ".join(row)
                )
                emit(_safe_links(text), "table_row")
        elif item := _LIST.match(line):
            flush()
            depth = len(item.group(1).expandtabs(4)) // 2
            emit(("  " * depth) + _safe_links(item.group(3)), "list_item")
        else:
            paragraph.append(stripped)
        index += 1
    flush()
    return atoms, sections
