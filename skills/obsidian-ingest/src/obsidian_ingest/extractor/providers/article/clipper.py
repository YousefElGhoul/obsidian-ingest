"""Web Clipper notes: a page the user saved with their own browser, as Markdown.

Obsidian Web Clipper writes YAML frontmatter (``title``, ``source``, ``author``,
``published``, ``created``...) followed by the page as Markdown. Only the fields this
tool uses are read, with a deliberately small parser: scalar values, quoted values and
simple lists. The body becomes the same atom kinds as fetched HTML, so links, images,
captions and formatting the clipper kept are preserved. The note is never modified.
"""

import re

from obsidian_ingest.extractor.html_text import SECTION_SEPARATOR, Atom, clean_text
from obsidian_ingest.notes import ClippedNote  # noqa: F401  (re-exported for providers)

MAX_NOTE_CHARACTERS = 2_000_000
_FIELDS = {"title", "source", "url", "author", "authors", "published", "created", "lang",
           "language", "description", "site"}  # fmt: skip
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^(```+|~~~+)\s*([\w+-]*)")
_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])[ \t]+(.*)$")
_IMAGE_LINE = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")
_WIKILINK = re.compile(r"^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$")


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
        item = re.match(r"^\s+-\s*(.*)$", line)
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


def markdown_atoms(body: str) -> tuple[list[Atom], list[tuple[str, int]]]:
    """Split clipped Markdown into atoms and (section path, first atom) pairs."""
    atoms: list[Atom] = []
    sections: list[tuple[str, int]] = []
    headings: list[tuple[int, str]] = []
    paragraph: list[str] = []

    def section() -> str | None:
        return SECTION_SEPARATOR.join(text for _, text in headings) or None

    def emit(text: str, kind: str) -> None:
        # Leading spaces carry list nesting; everything else is trimmed.
        text = text.rstrip() if kind == "list_item" else text.strip()
        if text.strip():
            atoms.append(Atom(text, kind, section()))

    def flush() -> None:
        if paragraph:
            text = " ".join(" ".join(line.split()) for line in paragraph)
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
            level, text = len(heading.group(1)), " ".join(heading.group(2).split())
            headings = [(lvl, t) for lvl, t in headings if lvl < level] + [(level, text)]
            used = sum(1 for path, _ in sections if path.split(" (")[0] == section())
            if used:
                headings[-1] = (level, f"{text} ({used + 1})")
            sections.append((section() or text, len(atoms)))
        elif not stripped:
            flush()
        elif stripped.startswith(">"):
            flush()
            quote = [stripped.lstrip("> ").strip()]
            while index + 1 < len(lines) and lines[index + 1].strip().startswith(">"):
                index += 1
                quote.append(lines[index].strip().lstrip("> ").strip())
            emit(" ".join(part for part in quote if part), "quote")
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
                emit(text, "table_row")
        elif item := _LIST.match(line):
            flush()
            depth = len(item.group(1).expandtabs(4)) // 2
            emit(("  " * depth) + item.group(3), "list_item")
        else:
            paragraph.append(stripped)
        index += 1
    flush()
    return atoms, sections
