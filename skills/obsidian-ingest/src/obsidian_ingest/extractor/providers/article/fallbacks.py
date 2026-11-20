"""In-page fallbacks for pages whose HTML extraction is empty, thin or truncated.

Each fallback finds the article text somewhere else in, or linked from, the same page,
without rendering JavaScript: the JSON-LD ``articleBody``, an embedded JSON HTML body
(Next.js ``__NEXT_DATA__`` and similar), the page's AMP version on the same host, or a
full-content entry for this page in its own RSS/Atom feed. Network fallbacks use the
same guarded fetcher, deadline and download budget as the page itself.
"""

import json
import re
from collections.abc import Iterator
from html import escape
from urllib.parse import urljoin, urlsplit

from lxml import etree

from obsidian_ingest.extractor.html_text import Extraction, clean_text, extract_html
from obsidian_ingest.extractor.http import FEED_TYPES, Deadline, Fetcher, FetchError
from obsidian_ingest.extractor.urls import URLRejected, check_url

_MIN_EMBEDDED_PARAGRAPHS = 3
_FEED_TYPES = {"application/rss+xml", "application/atom+xml"}
_ATOM = "{http://www.w3.org/2005/Atom}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _wrap(body_html: str) -> str:
    return f"<html><body><article>{body_html}</article></body></html>"


def json_ld_body(body: str | None, base_url: str) -> Extraction | None:
    """The JSON-LD articleBody: HTML when it has tags, otherwise plain paragraphs."""
    if not body or not body.strip():
        return None
    if "<p" not in body.lower():
        paragraphs = [p for p in re.split(r"\n\s*\n|\n", body) if p.strip()]
        body = "".join(f"<p>{escape(paragraph.strip())}</p>" for paragraph in paragraphs)
    return extract_html(_wrap(body), base_url)


def _strings(value: object) -> Iterator[str]:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)


def embedded_json_body(document: etree._Element, base_url: str) -> Extraction | None:
    """The longest HTML string with several paragraphs inside page-embedded JSON."""
    best = ""
    for script in document.iter("script"):
        kind = (script.get("type") or "").lower()
        if not script.text or (script.get("id") != "__NEXT_DATA__" and kind != "application/json"):
            continue
        try:
            data = json.loads(script.text)
        except ValueError:
            continue
        for text in _strings(data):
            if text.lower().count("<p") >= _MIN_EMBEDDED_PARAGRAPHS and len(text) > len(best):
                best = text
    return extract_html(_wrap(best), base_url) if best else None


def _same_site(host: str, candidate: str) -> bool:
    return candidate == host or candidate.endswith("." + host) or host.endswith("." + candidate)


def _join_link(base_url: str, href: str) -> str:
    try:
        return urljoin(base_url, href)
    except ValueError as error:
        raise URLRejected("URL_REJECTED: malformed fallback link") from error


def _link(document: etree._Element, rel: str, types: set[str] | None = None) -> list[str]:
    hrefs = []
    for link in document.iter("link"):
        rels = (link.get("rel") or "").lower().split()
        if (
            rel in rels
            and link.get("href")
            and (types is None or (link.get("type") or "").lower() in types)
        ):
            hrefs.append(link.get("href"))
    return hrefs


def amp_page(
    document: etree._Element, base_url: str, fetcher: Fetcher, deadline: Deadline
) -> Extraction | None:
    """The page's AMP version, only on the same host or a subdomain relation of it."""
    hrefs = _link(document, "amphtml")
    if not hrefs:
        return None
    host = urlsplit(base_url).hostname or ""
    page = fetcher.fetch(
        _join_link(base_url, hrefs[0]),
        deadline,
        allow_hop=lambda hop: _same_site(host, hop.host),
    )
    return extract_html(page.text, page.final_url)


def _entry_content(entry: etree._Element) -> tuple[list[str], str | None]:
    links = [entry.findtext("link") or ""]
    links += [item.get("href", "") for item in entry.iter(f"{_ATOM}link")]
    content = entry.findtext(_CONTENT) or entry.findtext(f"{_ATOM}content")
    return [link.strip() for link in links if link and link.strip()], content


def feed_entry(
    document: etree._Element,
    base_url: str,
    identities: set[str],
    fetcher: Fetcher,
    deadline: Deadline,
) -> Extraction | None:
    """This page's full-content entry from the feed the page itself links to."""
    hrefs = _link(document, "alternate", _FEED_TYPES)
    if not hrefs:
        return None
    host = urlsplit(base_url).hostname or ""
    feed = fetcher.fetch(
        _join_link(base_url, hrefs[0]),
        deadline,
        accept=FEED_TYPES,
        allow_hop=lambda hop: _same_site(host, hop.host),
    )
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(feed.body, parser)
    except etree.XMLSyntaxError as error:
        raise FetchError("NOT_HTML", f"the feed is not valid XML: {error}") from error
    for entry in [*root.iter("item"), *root.iter(f"{_ATOM}entry")]:
        links, content = _entry_content(entry)
        matched = False
        for link in links:
            try:
                matched = (
                    matched
                    or check_url(_join_link(feed.final_url, link)).identity_key in identities
                )
            except URLRejected:
                continue
        if matched:
            if not content or len(clean_text(content).split()) < 20:
                raise FetchError("NO_ARTICLE_TEXT", "the feed entry has no full content")
            return extract_html(_wrap(content), base_url)
    raise FetchError("NO_ARTICLE_TEXT", "the feed has no entry for this page")
