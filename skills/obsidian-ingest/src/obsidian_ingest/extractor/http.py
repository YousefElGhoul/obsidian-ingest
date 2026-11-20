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
import threading
import time
import zlib
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from email.message import Message
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from obsidian_ingest.extractor.urls import CheckedURL, check_url

MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_TOTAL_DECODED_BYTES = 20 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_REDIRECTS = 5
MAX_COMPRESSION_RATIO = 100
CONNECT_TIMEOUT = 10.0
REQUEST_TIMEOUT = 30.0
INGEST_DEADLINE = 60.0
MAX_RETRY_AFTER = 5.0
_CHUNK = 64 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_DNS_SLOTS = threading.BoundedSemaphore(4)
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
FEED_TYPES = frozenset(
    {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
)


class FetchError(ValueError):
    """A fetch that failed or was refused; ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


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


def blocked_address(address: str, *, allow_private_network: bool = False) -> bool:
    """Reject IPv4 transition aliases and, by default, non-public addresses."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        compatible = ip in _IPV4_COMPATIBLE and not (ip.is_unspecified or ip.is_loopback)
        if ip.ipv4_mapped or ip.sixtofour or compatible:
            return True
        if ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return not allow_private_network and (not ip.is_global or ip.is_multicast)


class _MeasuredHTTPResponse(http.client.HTTPResponse):
    header_bytes: int = 0

    def begin(self) -> None:
        # Count wire bytes before the header parser strips whitespace.
        stream = self.fp
        readline = stream.readline

        def measured(size: int) -> bytes:
            line = readline(min(size, MAX_HEADER_BYTES - self.header_bytes + 1))
            self.header_bytes += len(line)
            if self.header_bytes > MAX_HEADER_BYTES:
                raise FetchError("TOO_LARGE", f"headers are larger than {MAX_HEADER_BYTES} bytes")
            return line

        stream.readline = measured
        try:
            super().begin()
        finally:
            del stream.readline


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # The request may already have closed its socket.


@dataclass  # Mutable on purpose: it owns a live connection and its socket.
class _HTTPClientResponse:
    connection: http.client.HTTPConnection
    response: http.client.HTTPResponse
    sock: socket.socket
    shutdown_socket: socket.socket
    timer: threading.Timer
    status: int = field(init=False)
    headers: Message = field(init=False)

    def __post_init__(self) -> None:
        self.status = self.response.status
        self.headers = self.response.headers

    def read(self, size: int, timeout: float) -> bytes:
        if not self.response.isclosed():
            self.sock.settimeout(timeout)
        return self.response.read1(size) if size else b""

    def close(self) -> None:
        self.timer.cancel()
        self.shutdown_socket.close()
        self.response.close()
        self.connection.close()


class HTTPClientTransport:
    """The default transport: http.client, connected to the validated address."""

    def __init__(self) -> None:
        self._tls = ssl.create_default_context()

    def open(
        self, target: CheckedURL, address: str, headers: dict[str, str], timeout: float
    ) -> RawResponse:
        if target.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                target.host, target.port, context=self._tls
            )
        else:
            connection = http.client.HTTPConnection(target.host, target.port)
        connection.response_class = _MeasuredHTTPResponse
        parts = urlsplit(target.fetch_url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        sock = shutdown_socket = timer = None
        try:
            sock = socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET)
            # Keep a handle even when TLS or a close-delimited response takes ownership.
            shutdown_socket = sock.dup()
            timer = threading.Timer(timeout, _shutdown, args=(shutdown_socket,))
            timer.daemon = True
            timer.start()
            sock.settimeout(min(timeout, CONNECT_TIMEOUT))
            sock.connect((address, target.port))
            if target.scheme == "https":
                sock = self._tls.wrap_socket(sock, server_hostname=target.host)
            sock.settimeout(min(timeout, REQUEST_TIMEOUT))
            connection.sock = sock
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            return _HTTPClientResponse(connection, response, sock, shutdown_socket, timer)
        except (OSError, http.client.HTTPException, FetchError) as error:
            if timer is not None:
                timer.cancel()
            if shutdown_socket is not None:
                shutdown_socket.close()
            connection.close()
            if sock is not None:
                sock.close()
            if isinstance(error, FetchError):
                raise
            if isinstance(error, TimeoutError):
                raise FetchError("TIMEOUT", f"no response from {target.host}") from error
            if isinstance(error, http.client.LineTooLong):
                raise FetchError("TOO_LARGE", str(error)) from error
            # http.client has no dedicated exception for its header count limit.
            if type(error) is http.client.HTTPException and re.fullmatch(
                r"got more than \d+ headers", str(error)
            ):
                raise FetchError("TOO_LARGE", str(error)) from error
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


@dataclass  # Mutable on purpose: it tallies the bytes each ingest has read.
class Fetcher:
    """Fetch with every guard; resolver, transport and clock are injectable for tests."""

    resolver: Resolver = system_resolver
    transport: Transport = field(default_factory=HTTPClientTransport)
    allowed_ports: frozenset[int] = frozenset({80, 443})
    allow_private_network: bool = False
    user_agent: str = "obsidian-ingest (+https://github.com/YousefElGhoul/obsidian-ingest)"
    total_bytes: int = 0
    total_decoded_bytes: int = 0
    sleep: Callable[[float], None] = time.sleep

    def _addresses(self, target: CheckedURL, deadline: Deadline) -> list[str]:
        result: Future[list[str]] = Future()

        def resolve() -> None:
            try:
                result.set_result(self.resolver(target.host, target.port))
            except BaseException as error:  # noqa: BLE001 - propagate across the thread boundary
                result.set_exception(error)
            finally:
                _DNS_SLOTS.release()

        worker = threading.Thread(target=resolve, daemon=True)
        if not _DNS_SLOTS.acquire(timeout=deadline.check()):
            raise FetchError("TIMEOUT", "no DNS worker became available before the ingest deadline")
        try:
            deadline.check()
            worker.start()
        except (FetchError, RuntimeError):
            _DNS_SLOTS.release()
            raise
        worker.join(max(0, deadline.remaining()))
        deadline.check()
        if worker.is_alive():
            raise FetchError("TIMEOUT", "DNS did not finish before the ingest deadline")
        addresses = result.result()
        if not addresses:
            raise FetchError("DNS_FAILED", f"{target.host} has no address")
        if any(
            blocked_address(a, allow_private_network=self.allow_private_network) for a in addresses
        ):
            raise FetchError(
                "ADDRESS_BLOCKED",
                f"{target.host} resolves to a disallowed address",
            )
        return addresses

    def _open(self, target: CheckedURL, headers: dict[str, str], deadline: Deadline) -> RawResponse:
        """Connect to each validated address in order until one answers."""
        failure: FetchError | None = None
        for address in self._addresses(target, deadline):
            left = deadline.check()
            try:
                return self.transport.open(target, address, headers, left)
            except FetchError as error:
                deadline.check()
                if error.reason != "CONNECTION_FAILED":
                    raise
                failure = error  # Such as an IPv6 address on a host without IPv6.
        assert failure is not None
        raise failure

    def _read_body(self, response: RawResponse, deadline: Deadline) -> tuple[bytes, int]:
        encoding = (response.headers.get("content-encoding") or "identity").strip().lower()
        if encoding not in {"identity", "gzip", "deflate", "x-gzip"}:
            raise FetchError("NOT_HTML", f"unsupported content encoding {encoding}")
        gzip = encoding in {"gzip", "x-gzip"}
        inflater = None if encoding == "identity" else zlib.decompressobj(31 if gzip else 47)
        chunks: list[bytes] = []
        size = raw = 0
        while True:
            left = deadline.check()
            raw_left = MAX_TOTAL_BYTES - self.total_bytes
            if raw_left < 0:
                raise FetchError("TOO_LARGE", "the ingest's download budget is used up")
            decoded_left = MAX_TOTAL_DECODED_BYTES - self.total_decoded_bytes
            if decoded_left < 0:
                raise FetchError("TOO_LARGE", "the ingest's decoded budget is used up")
            output_limit = min(MAX_BODY_BYTES - size, decoded_left) + 1
            read_limit = min(_CHUNK, raw_left + 1)
            if inflater is None:
                read_limit = min(read_limit, output_limit)
            try:
                chunk = response.read(read_limit, min(left, REQUEST_TIMEOUT))
            except TimeoutError as error:
                raise FetchError("TIMEOUT", "the response body stalled") from error
            except (OSError, http.client.HTTPException) as error:
                deadline.check()
                raise FetchError(
                    "CONNECTION_FAILED", f"reading the body failed: {error}"
                ) from error
            raw += len(chunk)
            self.total_bytes += len(chunk)
            deadline.check()
            if not chunk:
                break
            if self.total_bytes > MAX_TOTAL_BYTES:
                raise FetchError("TOO_LARGE", "the ingest's download budget is used up")
            while chunk:
                deadline.check()
                decoded, chunk = chunk, b""
                if inflater is not None:
                    if gzip and inflater.eof:
                        inflater = zlib.decompressobj(31)
                    try:
                        decoded = inflater.decompress(decoded, output_limit)
                    except zlib.error as error:
                        raise FetchError(
                            "CONNECTION_FAILED", "the compressed body is invalid"
                        ) from error
                    if gzip:
                        chunk = inflater.unused_data
                size += len(decoded)
                self.total_decoded_bytes += len(decoded)
                if size > MAX_BODY_BYTES:
                    raise FetchError("TOO_LARGE", f"body is larger than {MAX_BODY_BYTES} bytes")
                if self.total_decoded_bytes > MAX_TOTAL_DECODED_BYTES:
                    raise FetchError("TOO_LARGE", "the ingest's decoded budget is used up")
                if inflater is not None and size > MAX_COMPRESSION_RATIO * raw + _CHUNK:
                    raise FetchError("TOO_LARGE", "compressed body expands too far")
                output_limit -= len(decoded)
                if decoded:
                    chunks.append(decoded)
        if inflater is not None and not inflater.eof:
            raise FetchError("CONNECTION_FAILED", "the compressed body ended early")
        return b"".join(chunks), raw

    def fetch(
        self,
        url: str,
        deadline: Deadline,
        accept: frozenset[str] = HTML_TYPES,
        *,
        allow_hop: Callable[[CheckedURL], bool] | None = None,
    ) -> FetchResult:
        """GET a URL with every check; raise FetchError (or URLRejected) on refusal."""
        current = check_url(url, self.allowed_ports)
        redirect_hosts: list[str] = []
        retried = False
        while True:
            deadline.check()
            if allow_hop is not None and not allow_hop(current):
                raise FetchError("URL_REJECTED", "the fallback points to another site")
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
                    try:
                        target = urljoin(current.fetch_url, response.headers["location"])
                        following = check_url(target, self.allowed_ports)
                    except ValueError as error:
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
            deadline.check()
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
