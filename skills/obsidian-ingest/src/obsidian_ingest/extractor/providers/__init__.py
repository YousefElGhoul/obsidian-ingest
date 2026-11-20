"""The provider boundary: one protocol and an ordered registry.

Everything specific to a source type (URL recognition, cache identity, acquisition,
provider-only metadata, citation rendering, thumbnails) lives in a provider package.
Shared code asks the registry for a provider and calls the protocol; it never names a
specific provider. Adding a source type means adding a package and one registry entry.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any, ClassVar, Protocol
from urllib.parse import urlparse

from obsidian_ingest.extractor import Provider, SourceKind
from obsidian_ingest.extractor.acquisition import Acquisition, OriginalCaption
from obsidian_ingest.extractor.metadata import ExclusionLookup, SourceMetadata
from obsidian_ingest.extractor.transcript import Transcript, TranscriptSegment

if TYPE_CHECKING:
    from obsidian_ingest.notes import ClippedNote, NoteRef
    from obsidian_ingest.source import Source


@dataclass(frozen=True, slots=True)
class ProviderContent:
    """One acquisition, normalized at the provider boundary for shared assembly."""

    metadata: SourceMetadata
    transcript: Transcript
    exclusions: ExclusionLookup
    acquisition: Acquisition
    original: OriginalCaption


@dataclass(frozen=True, slots=True)
class Citation:
    """A rendered `## Source` citation line: a label and its value.

    ``literal`` values (such as section names taken from a page) are untrusted text
    that the writer escapes; otherwise the value is provider-built Markdown (links).
    """

    label: str
    value: str
    literal: bool = False


class SourceProvider(Protocol):
    """What every source type implements; optional abilities raise UnsupportedOperation."""

    name: Provider
    kind: SourceKind
    supported: bool

    def matches_url(self, url: str) -> bool:
        """Return whether this provider recognizes the URL's host."""
        ...

    def identity(self, url: str) -> str:
        """Return the stable cache identity for a URL, or raise ValueError."""
        ...

    def acquire(
        self, url: str, *, exclude_sponsors: bool, previous: "Source | None" = None
    ) -> ProviderContent:
        """Fetch and normalize one capture. On refresh, ``previous`` is the current
        capture; a provider may raise NotModified when the source has not changed."""
        ...

    def exclusion_categories(self, exclude_sponsors: bool) -> tuple[str, ...]:
        """The exclusion categories a capture made with this option must record."""
        ...

    def encode_details(self, details: object | None) -> dict[str, Any] | None:
        """Serialize provider-only metadata for the cache."""
        ...

    def decode_details(self, data: dict[str, Any] | None) -> object | None:
        """Rebuild provider-only metadata from the cache, or raise ValueError."""
        ...

    def citation(
        self, metadata: SourceMetadata, segments: tuple[TranscriptSegment, ...]
    ) -> Citation | None:
        """Render the cited atoms as one `## Source` line, or None when nothing to show."""
        ...

    def attribution(self, metadata: SourceMetadata) -> tuple[tuple[str, str], ...]:
        """Labelled attribution lines (such as Creator or Author); the writer escapes them."""
        ...

    def thumbnail(self, metadata: SourceMetadata) -> tuple[bytes, str]:
        """Fetch a thumbnail as (bytes, url), or raise UnsupportedOperation/ValueError."""
        ...

    # Optional abilities; ProviderDefaults supplies the "not offered" behaviour.

    settings_table: ClassVar[str | None]
    """The trusted settings table this provider reads, if any."""

    def parse_settings(self, table: object) -> object:
        """Validate this provider's settings table (absent: ``{}``), or raise ValueError."""
        ...

    def configure(self, settings: object) -> None:
        """Apply settings returned by ``parse_settings``."""
        ...

    def accepts_note(self, text: str) -> bool:
        """Return whether a saved vault note (such as a web clip) is this provider's."""
        ...

    def note_identity(self, note: "ClippedNote") -> str:
        """The cache identity of the source a saved note captured."""
        ...

    def acquire_note(self, note: "ClippedNote", *, exclude_sponsors: bool) -> ProviderContent:
        """Build a capture from a saved note, which is never modified."""
        ...

    def required_note(self, source: "Source") -> "NoteRef | None":
        """The note a capture was built from, which readers must still be able to open."""
        ...

    def capture_matches_note(self, source: "Source", note: "ClippedNote") -> bool:
        """Whether a capture was built from exactly this note revision."""
        ...

    def fetched_identity(self, source: "Source") -> str:
        """The identity of the URL a capture was fetched from; its ``source_id`` is the
        identity it is stored under, which differs for a page naming a canonical URL."""
        ...


class UnsupportedOperation(ValueError):
    """A provider does not offer an optional ability, such as thumbnails."""


class NotModified(Exception):
    """A refresh found the source unchanged since the previous capture."""


class ProviderDefaults:
    """The optional abilities a provider does not offer: no settings and no saved notes."""

    __slots__ = ()
    settings_table: ClassVar[str | None] = None

    def parse_settings(self, table: object) -> object:
        return None

    def configure(self, settings: object) -> None:
        return None

    def accepts_note(self, text: str) -> bool:
        return False

    def note_identity(self, note: "ClippedNote") -> str:
        raise UnsupportedOperation("this source type has no saved-note captures")

    def acquire_note(self, note: "ClippedNote", *, exclude_sponsors: bool) -> ProviderContent:
        raise UnsupportedOperation("this source type has no saved-note captures")

    def required_note(self, source: "Source") -> "NoteRef | None":
        return None

    def capture_matches_note(self, source: "Source", note: "ClippedNote") -> bool:
        return False

    def fetched_identity(self, source: "Source") -> str:
        return source.metadata.source_id


@dataclass(frozen=True, slots=True)
class RecognizedProvider(ProviderDefaults):
    """A host the tool recognizes but cannot acquire yet; it fails explicitly."""

    name: Provider
    hosts: frozenset[str]
    kind: SourceKind = SourceKind.TIMED
    supported: bool = False

    def matches_url(self, url: str) -> bool:
        return (urlparse(url).hostname or "").lower() in self.hosts

    def _unsupported(self, *_: object, **__: object) -> Any:
        raise UnsupportedOperation(f"Source ingestion is not supported for provider: {self.name}")

    identity = acquire = thumbnail = _unsupported

    def exclusion_categories(self, exclude_sponsors: bool) -> tuple[str, ...]:
        return ()

    def encode_details(self, details: object | None) -> dict[str, Any] | None:
        return None

    def decode_details(self, data: dict[str, Any] | None) -> object | None:
        return None

    def citation(
        self, metadata: SourceMetadata, segments: tuple[TranscriptSegment, ...]
    ) -> Citation | None:
        return None

    def attribution(self, metadata: SourceMetadata) -> tuple[tuple[str, str], ...]:
        return ()


def _youtube() -> SourceProvider:
    from obsidian_ingest.extractor.providers.youtube.provider import YOUTUBE

    return YOUTUBE


def _instagram() -> SourceProvider:
    return RecognizedProvider(Provider.INSTAGRAM, frozenset({"instagram.com", "www.instagram.com"}))


def _tiktok() -> SourceProvider:
    return RecognizedProvider(
        Provider.TIKTOK,
        frozenset({"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com"}),
    )


def _article() -> SourceProvider:
    from obsidian_ingest.extractor.providers.article.provider import ARTICLE

    return ARTICLE


# Ordered: the first provider whose matches_url accepts a URL handles it. The article
# provider accepts any http(s) URL, so it must come last. Factories keep imports lazy.
_FACTORIES: tuple[Callable[[], SourceProvider], ...] = (_youtube, _instagram, _tiktok, _article)


@cache
def registry() -> tuple[SourceProvider, ...]:
    """Return every registered provider, in resolution order."""
    return tuple(factory() for factory in _FACTORIES)


def settings_tables() -> frozenset[str]:
    """The trusted settings tables providers read, beside the shared ``[vault]``."""
    return frozenset(p.settings_table for p in registry() if p.settings_table is not None)


def parse_settings(data: dict[str, Any]) -> tuple[tuple[SourceProvider, object], ...]:
    """Validate every provider's table of parsed trusted settings, or raise ValueError."""
    return tuple(
        (provider, provider.parse_settings(data.get(provider.settings_table, {})))
        for provider in registry()
        if provider.settings_table is not None
    )


def configure(data: dict[str, Any]) -> None:
    """Validate and apply every provider's table of parsed trusted settings."""
    for provider, settings in parse_settings(data):
        provider.configure(settings)


def resolve_note(text: str) -> SourceProvider:
    """Return the provider that can ingest a saved note (such as a web clip), or raise."""
    for provider in registry():
        if provider.accepts_note(text):
            return provider
    raise ValueError(
        "INVALID_ARGUMENT: this note is not a supported saved source; a Web Clipper note "
        "needs a source URL in its frontmatter"
    )


def resolve_url(url: str) -> SourceProvider:
    """Return the provider for a URL (supported or not), or raise ValueError."""
    if isinstance(url, str):
        for provider in registry():
            if provider.matches_url(url):
                return provider
    raise ValueError(f"Unsupported provider for URL: {url}")


def supported_provider(url: str) -> SourceProvider:
    """Return the provider that can acquire this URL, or raise ValueError."""
    provider = resolve_url(url)
    if not provider.supported:
        raise ValueError(f"Source ingestion is not supported for provider: {provider.name}")
    return provider


def get_provider(name: str | Provider) -> SourceProvider:
    """Return a registered provider by name, or raise ValueError."""
    for provider in registry():
        if provider.name == name:
            return provider
    raise ValueError(f"Unknown provider: {name}")


def required_note(source: "Source") -> "NoteRef | None":
    """Ask providers with saved-note captures which note must still be accessible."""
    return get_provider(source.metadata.provider).required_note(source)
