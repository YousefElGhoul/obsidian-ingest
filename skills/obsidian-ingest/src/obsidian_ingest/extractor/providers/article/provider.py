"""The web-article implementation of the source provider protocol.

Any http(s) URL no other provider claims is treated as an article. Acquisition is a
guarded fetch, sanitized extraction into Markdown atoms, metadata with provenance,
in-body exclusions and a quality gate; when the page's own extraction fails or is
evidently incomplete, in-page fallbacks are tried within the same deadline. A page
that still cannot be read fails with a stable reason and a hint to clip it instead.
"""

import hashlib
import ipaddress
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any
from urllib.parse import urlsplit

from lxml import etree

from obsidian_ingest.extractor import Provider, SourceKind
from obsidian_ingest.extractor.acquisition import Acquisition, CaptionFormat, OriginalCaption
from obsidian_ingest.extractor.exclusions import CATEGORIES, find_exclusions
from obsidian_ingest.extractor.html_text import Extraction, extract_html, parse
from obsidian_ingest.extractor.http import (
    Deadline,
    Fetcher,
    FetchError,
    FetchResult,
    blocked_address,
)
from obsidian_ingest.extractor.metadata import (
    Chapter,
    ExclusionLookup,
    ExclusionStatus,
    NativeFormat,
    SourceMetadata,
)
from obsidian_ingest.extractor.providers import (
    Citation,
    ProviderContent,
    UnsupportedOperation,
)
from obsidian_ingest.extractor.providers.article import clipper, fallbacks
from obsidian_ingest.extractor.providers.article.metadata import (
    PageMetadata,
    extract_metadata,
    parse_date,
)
from obsidian_ingest.extractor.quality import Quality, assess, instruction_like
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment
from obsidian_ingest.extractor.urls import URLRejected, check_url

LOG = logging.getLogger("obsidian_ingest.article")
MAX_CITED_SECTIONS = 8
OPENING_SECTION = "Opening"
CLIP_HINT = (
    "save the page with Obsidian Web Clipper (your browser can render it and use your "
    "logins), then ingest the clipped note"
)
_PAYWALL_PHRASES = re.compile(
    r"subscribe to (continue|read)|to continue reading|sign in to (read|continue)|"
    r"subscribers? only|already a subscriber|create a free account to (read|continue)",
    re.IGNORECASE,
)
_BLOCKED_REASONS = {"HTTP_401", "HTTP_403", "HTTP_429", "HTTP_451", "TIMEOUT", "CONNECTION_FAILED"}
_JS_SHELL = re.compile(r"""id=["'](__next|root|app|__nuxt)["']""")


@dataclass(frozen=True, slots=True)
class ArticleDetails:
    """Article-only metadata kept beside the shared source fields."""

    authors: tuple[str, ...]
    modified_at: datetime | None
    site_name: str | None
    final_url: str
    identity_key: str
    word_count: int | None


@dataclass(slots=True)
class _Candidate:
    method: str
    extraction: Extraction
    quality: Quality


# The trusted network policy, set from settings by ``configure`` (never by a tool call).
_policy: dict[str, Any] = {"allowed_ports": frozenset({80, 443}), "allow_private_network": False}


def _default_fetcher() -> Fetcher:
    return Fetcher(**_policy)


# Test seams: a fetcher (resolver/transport/clock) and the ingest deadline.
make_fetcher: Callable[[], Fetcher] = _default_fetcher
make_deadline: Callable[[], Deadline] = Deadline


def _paywall_signals(document: etree._Element, text: str, page: PageMetadata) -> tuple[str, ...]:
    signals = []
    if page.is_accessible_for_free is False:
        signals.append("json-ld")
    for element in document.iter():
        if not isinstance(element.tag, str):
            continue
        tokens = f"{element.get('class', '')} {element.get('id', '')}".lower()
        if "paywall" in tokens or "metered" in tokens:
            signals.append("container")
            break
    if _PAYWALL_PHRASES.search(re.sub(r"<[^>]+>", " ", text)):
        signals.append("prompt")
    return tuple(signals)


def _candidate(method: str, extraction: Extraction | None, **signals: Any) -> _Candidate | None:
    if extraction is None:
        return None
    texts = [atom.text for atom in extraction.atoms]
    duplicates = find_exclusions(texts, [atom.kind for atom in extraction.atoms], ("duplicate",))
    quality = assess(
        extraction.words,
        extraction.link_words,
        len(duplicates),
        len(texts),
        **signals,
    )
    return _Candidate(method, extraction, quality)


def _more_exists(page: PageMetadata, extraction: Extraction, html_text: str) -> bool:
    """Evidence that the page holds more text than its HTML extraction found."""
    words = extraction.words
    if page.word_count and words < 0.6 * page.word_count:
        return True
    if page.article_body and len(page.article_body.split()) > 1.5 * max(words, 1):
        return True
    return bool(_JS_SHELL.search(html_text)) and words < 150


def _sections(extraction: Extraction) -> tuple[Chapter, ...]:
    """Heading paths as half-open atom-index ranges; empty headings get no section."""
    bounds = [*extraction.sections, ("", len(extraction.atoms))]
    chapters = []
    for (path, start), (_, end) in pairwise(bounds):
        if end > start:
            chapters.append(Chapter(path, start, end))
    return tuple(chapters)


def _http_evidence(page: FetchResult) -> dict[str, Any]:
    return {
        "status": page.status,
        "content_type": page.content_type,
        "final_host": urlsplit(page.final_url).hostname,
        "redirect_hosts": list(page.redirect_hosts),
        "bytes": page.bytes_read,
        "encoding": page.encoding,
        "encoding_source": page.encoding_source,
        "replacement_characters": page.replacement_count,
    }


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _note_url(fields: dict) -> str | None:
    """The clipped page's URL from frontmatter, when it is an http(s) URL."""
    value = fields.get("source") or fields.get("url")
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, str) and urlsplit(value).scheme.lower() in {"http", "https"}:
        return value
    return None


def _clip_metadata(fields: dict, canonical: str) -> PageMetadata:
    """Metadata from Web Clipper frontmatter, with the same field rules as pages."""
    meta = PageMetadata(canonical_url=canonical)

    def text(name: str) -> str | None:
        value = fields.get(name)
        value = value[0] if isinstance(value, list) and value else value
        return value[:500] or None if isinstance(value, str) else None

    authors = fields.get("author") or fields.get("authors")
    authors = authors if isinstance(authors, list) else [authors] if authors else []
    meta.title = text("title")
    meta.authors = tuple(a[:200] for a in authors if isinstance(a, str) and a)[:20]
    meta.published = parse_date(text("published"))
    language = text("lang") or text("language")
    meta.language = (
        language
        if language and re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*", language)
        else None
    )
    meta.site_name = text("site")
    meta.description = text("description")
    meta.provenance = {
        name: "web-clipper"
        for name, value in (("title", meta.title), ("authors", meta.authors),
                            ("published", meta.published), ("language", meta.language),
                            ("site_name", meta.site_name), ("canonical_url", canonical))
        if value
    }  # fmt: skip
    return meta


@dataclass(frozen=True, slots=True)
class ArticleProvider:
    """Web articles: guarded fetch, extraction, fallbacks and section citations."""

    name: Provider = Provider.ARTICLE
    kind: SourceKind = SourceKind.TEXT
    supported: bool = True

    def matches_url(self, url: str) -> bool:
        return urlsplit(url).scheme.lower() in {"http", "https"} if isinstance(url, str) else False

    def identity(self, url: str) -> str:
        checked = check_url(url, make_fetcher().allowed_ports)
        return hashlib.sha256(checked.identity_key.encode()).hexdigest()[:16]

    def exclusion_categories(self, exclude_sponsors: bool) -> tuple[str, ...]:
        return CATEGORIES if exclude_sponsors else ()

    def acquire(self, url: str, *, exclude_sponsors: bool) -> ProviderContent:
        deadline = make_deadline()
        fetcher = make_fetcher()
        checked = check_url(url, fetcher.allowed_ports)
        try:
            page = fetcher.fetch(url, deadline)
        except FetchError as error:
            if error.reason in _BLOCKED_REASONS:
                # Bot blocking and logins: the user's own browser can still open the page.
                raise FetchError(
                    error.reason, f"{error.args[0].split(': ', 1)[1]}; {CLIP_HINT}"
                ) from error
            raise
        LOG.info(
            "stage=fetch host=%s status=%s bytes=%s redirects=%s encoding=%s seconds=%s",
            checked.host, page.status, page.bytes_read, len(page.redirect_hosts),
            page.encoding, deadline.used(),
        )  # fmt: skip
        document = parse(page.text)
        primary = extract_html(page.text, page.final_url)
        meta = extract_metadata(document, primary.doc)
        signals = {
            "declared_words": meta.word_count,
            "paywall_signals": _paywall_signals(document, page.text, meta),
            "replacement_count": page.replacement_count,
        }
        candidates = [_candidate("extract", primary, **signals)]
        attempts: list[dict[str, Any]] = []
        if candidates[0].quality.failed or _more_exists(meta, primary, page.text):
            identities = {checked.identity_key}
            if meta.canonical_url:
                try:
                    identities.add(check_url(meta.canonical_url).identity_key)
                except URLRejected:
                    pass
            for method, attempt in (
                ("json-ld", lambda: fallbacks.json_ld_body(meta.article_body, page.final_url)),
                ("embedded-json", lambda: fallbacks.embedded_json_body(document, page.final_url)),
                ("amp", lambda: fallbacks.amp_page(document, page.final_url, fetcher, deadline)),
                (
                    "feed",
                    lambda: fallbacks.feed_entry(
                        document, page.final_url, identities, fetcher, deadline
                    ),
                ),
            ):
                try:
                    found = _candidate(method, attempt(), **signals | {"declared_words": None})
                except FetchError as error:
                    if error.reason == "TIMEOUT":
                        raise
                    attempts.append({"method": method, "status": "failed", "reason": str(error)})
                    continue
                if found is None:
                    continue
                candidates.append(found)
                if found.quality.status == "ok":
                    break
        for candidate in candidates:
            attempts.insert(
                candidates.index(candidate),
                {
                    "method": candidate.method,
                    "status": candidate.quality.status,
                    "words": candidate.quality.words,
                    "reason": candidate.quality.reason,
                },
            )
        best = max(candidates, key=lambda candidate: candidate.quality.score())
        for attempt in attempts:
            LOG.info(
                "stage=extract method=%s status=%s words=%s reason=%s",
                attempt["method"], attempt["status"], attempt.get("words"), attempt["reason"],
            )  # fmt: skip
        if best.quality.failed:
            detail = {
                "NO_ARTICLE_TEXT": "no readable article text was found",
                "PAYWALL_SUSPECTED": "the page looks paywalled or sign-in only",
                "LOW_QUALITY": "the page text is mostly navigation or repeated blocks",
            }[best.quality.reason]
            raise FetchError(best.quality.reason, f"{detail}; {CLIP_HINT}")
        return self._content(
            url,
            checked.identity_key,
            meta,
            best,
            OriginalCaption(CaptionFormat.HTML, page.body),
            exclude_sponsors,
            final_url=page.final_url,
            evidence={
                "attempts": attempts,
                "http": _http_evidence(page),
                "deadline_used_seconds": deadline.used(),
            },
        )

    def accepts_note(self, text: str) -> bool:
        fields, _ = clipper.split_frontmatter(text)
        return isinstance(_note_url(fields), str)

    def note_identity(self, note: clipper.ClippedNote) -> str:
        fields, _ = clipper.split_frontmatter(note.text)
        return self.identity(_note_url(fields))

    def acquire_note(self, note: clipper.ClippedNote, *, exclude_sponsors: bool) -> ProviderContent:
        """Build a capture from a Web Clipper note; the note itself is never modified."""
        if len(note.text) > clipper.MAX_NOTE_CHARACTERS:
            raise ValueError("INVALID_ARGUMENT: the note is too long to ingest")
        fields, body = clipper.split_frontmatter(note.text)
        url = _note_url(fields)
        checked = check_url(url, make_fetcher().allowed_ports)
        if _is_ip_literal(checked.host) and blocked_address(checked.host):
            raise URLRejected("URL_REJECTED: the clip's source is a private or local address")
        atoms, sections = clipper.markdown_atoms(body)
        extraction = Extraction(atoms=atoms, sections=sections)
        for atom in atoms:
            extraction.words += len(atom.text.split())
            for label in re.findall(r"(?<!!)\[([^\]]*)\]\(https?://", atom.text):
                extraction.links += 1
                extraction.link_words += len(label.split())
            extraction.images += len(re.findall(r"!\[[^\]]*\]\(https?://", atom.text))
        meta = _clip_metadata(fields, checked.fetch_url)
        candidate = _candidate("web_clipper", extraction, declared_words=None)
        if candidate.quality.failed:
            raise FetchError(
                candidate.quality.reason, "the clipped note has no readable article text"
            )
        return self._content(
            url,
            checked.identity_key,
            meta,
            candidate,
            OriginalCaption(CaptionFormat.MARKDOWN, note.text.encode("utf-8")),
            exclude_sponsors,
            final_url=checked.fetch_url,
            evidence={"note_path": note.path, "note_revision": note.revision},
        )

    def _content(
        self,
        url: str,
        identity_key: str,
        meta: PageMetadata,
        best: _Candidate,
        original: OriginalCaption,
        exclude_sponsors: bool,
        *,
        final_url: str,
        evidence: dict[str, Any],
    ) -> ProviderContent:
        import trafilatura

        extraction = best.extraction
        source_id = hashlib.sha256(identity_key.encode()).hexdigest()[:16]
        texts = [atom.text for atom in extraction.atoms]
        kinds = [atom.kind for atom in extraction.atoms]
        categories = self.exclusion_categories(exclude_sponsors)
        acquired_at = datetime.now(UTC)
        lookup = ExclusionLookup(
            ranges=find_exclusions(texts, kinds, categories),
            status=ExclusionStatus.COMPLETE if categories else ExclusionStatus.DISABLED,
            provenance="obsidian-ingest text rules",
            categories=categories,
            retrieved_at=acquired_at if categories else None,
        )
        canonical = final_url
        if meta.canonical_url:
            try:
                canonical = check_url(meta.canonical_url).fetch_url
            except URLRejected:
                pass
        language = meta.language or "und"
        metadata = SourceMetadata(
            provider=self.name,
            source_id=source_id,
            original_url=url,
            canonical_url=canonical,
            title=meta.title,
            description=meta.description,
            creator_name=", ".join(meta.authors) or None,
            creator_id=None,
            creator_url=None,
            duration_seconds=None,
            published_at=meta.published,
            language=meta.language,
            tags=(),
            chapters=_sections(extraction),
            native_format=NativeFormat.STANDARD,
            provider_details=ArticleDetails(
                authors=meta.authors,
                modified_at=meta.modified,
                site_name=meta.site_name,
                final_url=final_url,
                identity_key=identity_key,
                word_count=meta.word_count,
            ),
        )
        transcript = Transcript(
            source_id,
            language,
            language,
            False,
            tuple(
                TranscriptSegment(atom.text, None, None, atom.section, atom.kind)
                for atom in extraction.atoms
            ),
            SourceKind.TEXT,
        )
        evidence = {
            "method": best.method,
            **evidence,
            "quality": {
                "status": best.quality.status,
                "words": best.quality.words,
                "link_density": best.quality.link_density,
                "duplicate_ratio": best.quality.duplicate_ratio,
                "warnings": list(best.quality.warnings),
            },
            "instruction_like": instruction_like(texts),
            "removed": {
                "hidden_elements": extraction.removed_hidden,
                "chrome_elements": extraction.removed_chrome,
            },
            "kept": {"links": extraction.links, "images": extraction.images},
            "metadata_provenance": dict(meta.provenance),
            "metadata_conflicts": {key: list(value) for key, value in meta.conflicts.items()},
            "original_payload_format": original.format.value,
            "original_payload_bytes": len(original.data),
            "original_payload_sha256": original.sha256,
        }
        acquisition = Acquisition(
            acquired_at, "trafilatura", trafilatura.__version__, None, evidence
        )
        return ProviderContent(metadata, transcript, lookup, acquisition, original)

    def encode_details(self, details: object | None) -> dict[str, Any] | None:
        if details is None:
            return None
        if not isinstance(details, ArticleDetails):
            raise TypeError("unexpected provider details for an article")
        data = asdict(details)
        data["authors"] = list(details.authors)
        data["modified_at"] = details.modified_at.isoformat() if details.modified_at else None
        return data

    def decode_details(self, data: dict[str, Any] | None) -> object | None:
        if data is None:
            return None
        expected = {"authors", "modified_at", "site_name", "final_url", "identity_key",
                    "word_count"}  # fmt: skip
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("malformed article details")
        authors = data["authors"]
        if not isinstance(authors, list) or not all(isinstance(a, str) for a in authors):
            raise ValueError("malformed article authors")
        for key in ("site_name",):
            if data[key] is not None and not isinstance(data[key], str):
                raise ValueError(f"malformed article detail: {key}")
        for key in ("final_url", "identity_key"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError(f"malformed article detail: {key}")
        count = data["word_count"]
        if count is not None and (isinstance(count, bool) or not isinstance(count, int)):
            raise ValueError("malformed article word count")
        modified = data["modified_at"]
        if modified is not None and not isinstance(modified, str):
            raise ValueError("malformed article modification date")
        return ArticleDetails(
            authors=tuple(authors),
            modified_at=parse_date(modified) if modified else None,
            site_name=data["site_name"],
            final_url=data["final_url"],
            identity_key=data["identity_key"],
            word_count=count,
        )

    def citation(
        self, metadata: SourceMetadata, segments: tuple[TranscriptSegment, ...]
    ) -> Citation | None:
        names: list[str] = []
        for segment in segments:
            name = segment.section or OPENING_SECTION
            if name not in names:
                names.append(name)
        if not names:
            return None
        return Citation("Relevant sections", "; ".join(names[:MAX_CITED_SECTIONS]), literal=True)

    def attribution(self, metadata: SourceMetadata) -> tuple[tuple[str, str], ...]:
        lines = []
        details = metadata.provider_details
        authors = details.authors if isinstance(details, ArticleDetails) else ()
        if authors:
            lines.append(("Author" if len(authors) == 1 else "Authors", ", ".join(authors)))
        if isinstance(details, ArticleDetails) and details.site_name:
            lines.append(("Site", details.site_name))
        if metadata.published_at:
            lines.append(("Published", metadata.published_at.date().isoformat()))
        if metadata.language:
            lines.append(("Language", metadata.language))
        return tuple(lines)

    def thumbnail(self, metadata: SourceMetadata) -> tuple[bytes, str]:
        raise UnsupportedOperation("articles have no thumbnail")

    def configure(self, options: dict[str, Any]) -> None:
        """Apply the trusted network policy (``allow_private_network``, ``extra_ports``)."""
        _policy["allow_private_network"] = bool(options.get("allow_private_network", False))
        _policy["allowed_ports"] = frozenset({80, 443, *options.get("extra_ports", ())})


ARTICLE = ArticleProvider()
