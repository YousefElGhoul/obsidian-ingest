"""Guarded HTTP fetching for text sources: SSRF defences, limits, deadline, decoding.

Every hop is validated before connecting: the URL (``urls.check_url``), then every
address the host resolves to. The connection goes to the validated address while the
hostname is kept for TLS (SNI and certificate checks) and the Host header, so a second
DNS answer cannot redirect it (DNS rebinding). Redirects are followed manually so each
target gets the same checks. Environment proxies, cookies and credentials are never
used. Bodies are capped after decompression, and one deadline covers every request of
an ingest. Failures raise ``FetchError`` whose message starts with a stable reason.
"""

import codecs
import http.client
import ipaddress
import re
import socket
import ssl
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from obsidian_ingest.extractor.urls import CheckedURL, URLRejected, check_url

MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_COMPRESSION_RATIO = 100
CONNECT_TIMEOUT = 10.0
REQUEST_TIMEOUT = 30.0
INGEST_DEADLINE = 60.0
MAX_RETRY_AFTER = 5.0
_CHUNK = 64 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
FEED_TYPES = frozenset(
    {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
)


class FetchError(ValueError):
    """A fetch that failed or was refused; ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


class Deadline:
    """One time budget shared by every request of an ingest."""

    def __init__(
        self, seconds: float = INGEST_DEADLINE, clock: Callable[[], float] = time.monotonic
    ):
        self._clock = clock
        self._end = clock() + seconds
        self.seconds = seconds

    def remaining(self) -> float:
        return self._end - self._clock()

    def check(self) -> float:
        """Return the remaining seconds, or raise TIMEOUT when none are left."""
        left = self.remaining()
        if left <= 0:
            raise FetchError("TIMEOUT", f"the {self.seconds:g} s ingest deadline passed")
        return left

    def used(self) -> float:
        return round(self.seconds - self.remaining(), 3)


class RawResponse(Protocol):
    """What a transport returns: status, headers and a timed, bounded body reader."""

    status: int
    headers: Message

    def read(self, size: int, timeout: float) -> bytes: ...

    def close(self) -> None: ...


class Transport(Protocol):
    """Open one request to an already-validated address."""

    def open(
        self, target: CheckedURL, address: str, headers: dict[str, str], timeout: float
    ) -> RawResponse: ...


Resolver = Callable[[str, int], list[str]]


def system_resolver(host: str, port: int) -> list[str]:
    """Every address the system resolver returns for the host."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise FetchError("DNS_FAILED", f"could not resolve {host}: {error}") from error
    # Keep the system's preference order (RFC 6724), without duplicates.
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def blocked_address(address: str) -> bool:
    """True for any address that is not on the public internet."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip in _NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None and not embedded.is_global:
            return True
    return not ip.is_global or ip.is_multicast


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, address: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, address: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        # SNI and certificate verification use the hostname, not the pinned address.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


@dataclass
class _HTTPClientResponse:
    connection: http.client.HTTPConnection
    response: http.client.HTTPResponse
    status: int = field(init=False)
    headers: Message = field(init=False)

    def __post_init__(self) -> None:
        self.status = self.response.status
        self.headers = self.response.headers

    def read(self, size: int, timeout: float) -> bytes:
        if self.connection.sock is not None:
            self.connection.sock.settimeout(max(timeout, 0.001))
        return self.response.read1(size) if size else b""

    def close(self) -> None:
        self.connection.close()


class HTTPClientTransport:
    """The default transport: http.client, connected to the validated address."""

    def __init__(self) -> None:
        self._tls = ssl.create_default_context()

    def open(
        self, target: CheckedURL, address: str, headers: dict[str, str], timeout: float
    ) -> RawResponse:
        connect_timeout = min(timeout, CONNECT_TIMEOUT)
        if target.scheme == "https":
            connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
                address, target.host, target.port, timeout=connect_timeout, context=self._tls
            )
        else:
            connection = _PinnedHTTPConnection(
                address, target.host, target.port, timeout=connect_timeout
            )
        parts = urlsplit(target.fetch_url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        try:
            connection.request("GET", path, headers=headers)
            return _HTTPClientResponse(connection, connection.getresponse())
        except (OSError, http.client.HTTPException) as error:
            connection.close()
            if isinstance(error, TimeoutError | socket.timeout):
                raise FetchError("TIMEOUT", f"no response from {target.host}") from error
            raise FetchError("CONNECTION_FAILED", f"{target.host}: {error}") from error


@dataclass(frozen=True, slots=True)
class FetchResult:
    """A fetched, decoded response and what it took to get it."""

    requested_url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    text: str
    encoding: str
    encoding_source: str
    replacement_count: int
    redirect_hosts: tuple[str, ...]
    bytes_read: int


def _charset(headers: Message) -> tuple[str, str | None]:
    """The media type and charset parameter of a Content-Type header."""
    message = Message()
    message["content-type"] = headers.get("content-type", "")
    return message.get_content_type(), message.get_param("charset")


def _meta_charset(body: bytes) -> str | None:
    """A charset declared in the first 4 KiB of HTML, if any."""
    head = body[:4096].decode("ascii", "replace")
    match = re.search(
        r"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", head, re.IGNORECASE
    )
    return match.group(1) if match else None


def decode(body: bytes, header_charset: str | None) -> tuple[str, str, str, int]:
    """Decode bytes as (text, encoding, where the encoding came from, replacements)."""
    for bom, name in (
        (codecs.BOM_UTF8, "utf-8-sig"),
        (codecs.BOM_UTF16_LE, "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16"),
    ):
        if body.startswith(bom):
            candidates = [(name, "bom")]
            break
    else:
        candidates = []
        if header_charset:
            candidates.append((header_charset, "http"))
        meta = _meta_charset(body)
        if meta:
            candidates.append((meta, "meta"))
    for name, source in candidates:
        try:
            codecs.lookup(name)
        except LookupError:
            continue
        text = body.decode(name, "replace")
        return text, codecs.lookup(name).name, source, text.count("�")
    try:
        text = body.decode("utf-8")
        return text, "utf-8", "default", 0
    except UnicodeDecodeError:
        pass
    from charset_normalizer import from_bytes

    best = from_bytes(body).best()
    if best is not None and best.encoding:
        text = str(best)
        return text, best.encoding, "detected", text.count("�")
    text = body.decode("utf-8", "replace")
    return text, "utf-8", "default", text.count("�")


@dataclass
class Fetcher:
    """Fetch with every guard; resolver, transport and clock are injectable for tests."""

    resolver: Resolver = system_resolver
    transport: Transport = field(default_factory=HTTPClientTransport)
    allowed_ports: frozenset[int] = frozenset({80, 443})
    allow_private_network: bool = False
    user_agent: str = "obsidian-ingest (+https://github.com/YousefElGhoul/obsidian-ingest)"
    total_bytes: int = 0
    sleep: Callable[[float], None] = time.sleep

    def _addresses(self, target: CheckedURL) -> list[str]:
        addresses = self.resolver(target.host, target.port)
        if not addresses:
            raise FetchError("DNS_FAILED", f"{target.host} has no address")
        if not self.allow_private_network and any(blocked_address(a) for a in addresses):
            raise FetchError(
                "ADDRESS_BLOCKED",
                f"{target.host} resolves to a private, local or reserved address",
            )
        return addresses

    def _open(self, target: CheckedURL, headers: dict[str, str], deadline: Deadline) -> RawResponse:
        """Connect to each validated address in order until one answers."""
        failure: FetchError | None = None
        for address in self._addresses(target):
            left = deadline.check()
            try:
                return self.transport.open(target, address, headers, min(left, REQUEST_TIMEOUT))
            except FetchError as error:
                if error.reason != "CONNECTION_FAILED":
                    raise
                failure = error  # Such as an IPv6 address on a host without IPv6.
        assert failure is not None
        raise failure

    def _read_body(self, response: RawResponse, deadline: Deadline) -> tuple[bytes, int]:
        encoding = (response.headers.get("content-encoding") or "identity").strip().lower()
        if encoding not in {"identity", "gzip", "deflate", "x-gzip"}:
            raise FetchError("NOT_HTML", f"unsupported content encoding {encoding}")
        inflater = None if encoding == "identity" else zlib.decompressobj(47)
        chunks: list[bytes] = []
        size = raw = 0
        while True:
            left = deadline.check()
            try:
                chunk = response.read(_CHUNK, min(left, REQUEST_TIMEOUT))
            except TimeoutError as error:
                raise FetchError("TIMEOUT", "the response body stalled") from error
            except (OSError, http.client.HTTPException) as error:
                raise FetchError(
                    "CONNECTION_FAILED", f"reading the body failed: {error}"
                ) from error
            if not chunk:
                break
            raw += len(chunk)
            if inflater is not None:
                chunk = inflater.decompress(chunk, MAX_BODY_BYTES - size + 1)
                if inflater.unconsumed_tail or size + len(chunk) > MAX_BODY_BYTES:
                    raise FetchError("TOO_LARGE", f"body is larger than {MAX_BODY_BYTES} bytes")
                if size + len(chunk) > MAX_COMPRESSION_RATIO * raw + _CHUNK:
                    raise FetchError("TOO_LARGE", "compressed body expands too far")
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise FetchError("TOO_LARGE", f"body is larger than {MAX_BODY_BYTES} bytes")
            if self.total_bytes + raw > MAX_TOTAL_BYTES:
                raise FetchError("TOO_LARGE", "the ingest's download budget is used up")
            chunks.append(chunk)
        self.total_bytes += raw
        return b"".join(chunks), raw

    def fetch(
        self, url: str, deadline: Deadline, accept: frozenset[str] = HTML_TYPES
    ) -> FetchResult:
        """GET a URL with every check; raise FetchError (or URLRejected) on refusal."""
        current = check_url(url, self.allowed_ports)
        redirect_hosts: list[str] = []
        retried = False
        while True:
            deadline.check()
            headers = {
                "User-Agent": self.user_agent,
                "Accept": ", ".join(sorted(accept)) + ";q=1.0, */*;q=0.1",
                "Accept-Encoding": "gzip, deflate",
                "Accept-Language": "*",
            }
            response = self._open(current, headers, deadline)
            try:
                deadline.check()  # A response that arrives after the deadline is too late.
                status = response.status
                if status in _REDIRECTS and response.headers.get("location"):
                    if len(redirect_hosts) >= MAX_REDIRECTS:
                        raise FetchError("TOO_MANY_REDIRECTS", f"more than {MAX_REDIRECTS}")
                    target = urljoin(current.fetch_url, response.headers["location"])
                    try:
                        following = check_url(target, self.allowed_ports)
                    except URLRejected as error:
                        raise FetchError("URL_REJECTED", f"redirect target: {error}") from error
                    if current.scheme == "https" and following.scheme == "http":
                        raise FetchError("URL_REJECTED", "redirect downgrades https to http")
                    redirect_hosts.append(following.host)
                    current = following
                    continue
                if status in {429, 503} and not retried:
                    wait = _retry_after(response.headers.get("retry-after"))
                    if wait is not None and wait <= MAX_RETRY_AFTER and wait < deadline.remaining():
                        retried = True
                        self.sleep(wait)
                        continue
                if status != 200:
                    raise FetchError(f"HTTP_{status}", f"{current.host} answered {status}")
                content_type, charset = _charset(response.headers)
                if content_type not in accept:
                    raise FetchError("NOT_HTML", f"content type is {content_type or 'missing'}")
                body, raw = self._read_body(response, deadline)
            finally:
                response.close()
            text, encoding, source, replacements = decode(body, charset)
            return FetchResult(
                requested_url=url,
                final_url=current.fetch_url,
                status=status,
                content_type=content_type,
                body=body,
                text=text,
                encoding=encoding,
                encoding_source=source,
                replacement_count=replacements,
                redirect_hosts=tuple(redirect_hosts),
                bytes_read=raw,
            )


def _retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header given in seconds; dates are not honoured."""
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
