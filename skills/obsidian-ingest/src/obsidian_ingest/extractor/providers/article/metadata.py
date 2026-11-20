"""Article metadata from the page itself, with the provenance of every field.

Sources, in precedence order: JSON-LD (Article, NewsArticle, BlogPosting and similar),
trafilatura's metadata, OpenGraph and ``article:*``/``meta`` tags, then ``<title>``.
A field no source supplies stays None; nothing is guessed. Conflicting values are
recorded so the evidence shows them.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from typing import Any

from lxml import etree

from obsidian_ingest.extractor.html_text import clean_text

_ARTICLE_TYPES = {
    "article", "newsarticle", "blogposting", "techarticle", "scholarlyarticle", "report",
    "analysisnewsarticle", "opinionnewsarticle", "reportagenewsarticle", "webpage",
}  # fmt: skip
_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
MAX_FIELD_CHARS = 500


@dataclass(slots=True)
class PageMetadata:
    """Article fields plus where each came from and any conflicts between sources."""

    title: str | None = None
    authors: tuple[str, ...] = ()
    published: datetime | None = None
    modified: datetime | None = None
    canonical_url: str | None = None
    language: str | None = None
    site_name: str | None = None
    description: str | None = None
    word_count: int | None = None
    is_accessible_for_free: bool | None = None
    article_body: str | None = None
    provenance: dict[str, str] = field(default_factory=dict)
    conflicts: dict[str, list[str]] = field(default_factory=dict)


def _text(value: object) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value")
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = " ".join(clean_text(unescape(str(value))).split())
    return text[:MAX_FIELD_CHARS] or None


def _authors(value: object) -> tuple[str, ...]:
    items = value if isinstance(value, list) else [value]
    names = []
    for item in items:
        name = _text(item)
        if name and name not in names:
            names.append(name)
    return tuple(names[:20])


def parse_date(value: object) -> datetime | None:
    """Parse an ISO 8601 date or date-time; a date alone means midnight UTC."""
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _json_ld_objects(document: etree._Element) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in document.iter("script"):
        if (script.get("type") or "").lower() != "application/ld+json" or not script.text:
            continue
        try:
            data = json.loads(script.text)
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                objects.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
    return objects


def _is_article(item: dict[str, Any]) -> bool:
    kinds = item.get("@type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    return any(isinstance(kind, str) and kind.lower() in _ARTICLE_TYPES for kind in kinds)


def _meta(document: etree._Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for meta in document.iter("meta"):
        key = (meta.get("property") or meta.get("name") or "").strip().lower()
        content = meta.get("content")
        if key and content and key not in values:
            values[key] = content
    return values


def extract_metadata(document: etree._Element, doc: dict[str, str]) -> PageMetadata:
    """Merge every metadata source of an unsanitized document; ``doc`` is trafilatura's."""
    page = PageMetadata()
    candidates: dict[str, list[tuple[str, object]]] = {}

    def offer(name: str, source: str, value: object) -> None:
        if value not in (None, (), ""):
            candidates.setdefault(name, []).append((source, value))

    articles = [item for item in _json_ld_objects(document) if _is_article(item)]
    # Prefer a specific article type over a generic WebPage.
    articles.sort(key=lambda item: str(item.get("@type")).lower() == "webpage")
    for item in articles[:1]:
        offer("title", "json-ld", _text(item.get("headline") or item.get("name")))
        offer("authors", "json-ld", _authors(item.get("author")))
        offer("published", "json-ld", parse_date(item.get("datePublished")))
        offer("modified", "json-ld", parse_date(item.get("dateModified")))
        offer("language", "json-ld", _text(item.get("inLanguage")))
        offer("description", "json-ld", _text(item.get("description")))
        publisher = item.get("publisher")
        offer("site_name", "json-ld", _text(publisher) if publisher else None)
        url = item.get("url") or item.get("mainEntityOfPage")
        offer("canonical_url", "json-ld", _text(url.get("@id") if isinstance(url, dict) else url))
        count = item.get("wordCount")
        if isinstance(count, int) and not isinstance(count, bool) and count > 0:
            offer("word_count", "json-ld", count)
        free = item.get("isAccessibleForFree")
        if isinstance(free, str):
            free = {"true": True, "false": False}.get(free.lower())
        if isinstance(free, bool):
            offer("is_accessible_for_free", "json-ld", free)
        body = item.get("articleBody")
        if isinstance(body, str) and body.strip():
            page.article_body = body

    offer("title", "trafilatura", _text(doc.get("title")))
    if doc.get("author"):
        offer("authors", "trafilatura", _authors([a.strip() for a in doc["author"].split(";")]))
    offer("published", "trafilatura", parse_date(doc.get("date")))
    offer("description", "trafilatura", _text(doc.get("description")))

    meta = _meta(document)
    offer("title", "opengraph", _text(meta.get("og:title")))
    offer("authors", "meta", _authors(meta.get("author") or meta.get("article:author")))
    offer("published", "meta", parse_date(meta.get("article:published_time")))
    offer("modified", "meta", parse_date(meta.get("article:modified_time")))
    offer("site_name", "opengraph", _text(meta.get("og:site_name")))
    offer("description", "opengraph", _text(meta.get("og:description") or meta.get("description")))
    offer("canonical_url", "opengraph", _text(meta.get("og:url")))
    locale = _text(meta.get("og:locale"))
    offer("language", "opengraph", locale.replace("_", "-") if locale else None)
    for link in document.iter("link"):
        if "canonical" in (link.get("rel") or "").lower().split() and link.get("href"):
            offer("canonical_url", "link", _text(link.get("href")))
            break
    html_root = document if document.tag == "html" else document.getroottree().getroot()
    offer("language", "html", _text(html_root.get("lang") or html_root.get("xml:lang")))
    title = document.find(".//title")
    offer("title", "html", _text(title.text_content()) if title is not None else None)

    for name, offers in candidates.items():
        if name == "language":
            offers = [(s, v) for s, v in offers if isinstance(v, str) and _LANGUAGE.match(v)]
        if not offers:
            continue
        source, value = offers[0]
        setattr(page, name, value)
        page.provenance[name] = source
        others = sorted({str(v) for _, v in offers[1:] if str(v) != str(value)})
        if others and name not in {"description", "title"}:
            page.conflicts[name] = [str(value), *others]
    return page
