"""URL validation and the three URL forms a fetched source keeps apart.

- ``original_url``: exactly what the user gave; shown in drafts.
- ``fetch_url``: the original with only what is needed to request it safely (IDNA host,
  percent-encoded unsafe characters, no fragment). The query and its order are kept,
  because pages can depend on their parameters.
- ``identity_key``: for the cache only. Normalized so trivial variants (tracking
  parameters, fragments, host case, default ports, query order) share one capture.

Validation happens before any network access; it rejects what cannot be fetched safely.
Address checks (private networks, DNS) happen at fetch time in ``http``.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
DEFAULT_PORTS = {"http": 80, "https": 443}
# Parameters that identify a click or campaign, never the content of the page.
_TRACKING = re.compile(r"^(utm_[a-z0-9_]+|fbclid|gclid|dclid|msclkid|mc_eid|mc_cid|igshid)$")
# Hosts written as numbers in a non-dotted-quad form (2130706433, 0x7f.1, 0177.0.0.1, 127.1)
# are legal to some resolvers and a common way to smuggle loopback past naive checks.
_NUMERIC_HOST = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+))*\.?$")
_DOTTED_QUAD = re.compile(
    r"^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$"
)
# Characters allowed unencoded in a path or query, besides already-encoded triplets.
_PATH_SAFE = "/:@!$&'()*+,;=-._~%"
_QUERY_SAFE = _PATH_SAFE + "?"


class URLRejected(ValueError):
    """A URL that must not be fetched; the message says why."""


@dataclass(frozen=True, slots=True)
class CheckedURL:
    """A validated URL split into the parts fetching needs."""

    original: str
    scheme: str
    host: str  # IDNA (ASCII) form; IPv6 literals without brackets
    port: int
    fetch_url: str
    identity_key: str


def _ascii_host(host: str) -> str:
    """Return the IDNA form of a hostname, or raise URLRejected."""
    if host.startswith("[") or ":" in host:
        return host.strip("[]")
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise URLRejected(f"URL_REJECTED: invalid host name {host!r}") from error
    if _NUMERIC_HOST.fullmatch(ascii_host) and not _DOTTED_QUAD.fullmatch(ascii_host):
        raise URLRejected(f"URL_REJECTED: numeric host {host!r} is not a plain IP address")
    return ascii_host


def check_url(url: object, allowed_ports: frozenset[int] = frozenset({80, 443})) -> CheckedURL:
    """Validate a URL for fetching and derive its fetch URL and identity key."""
    if not isinstance(url, str) or not url:
        raise URLRejected("URL_REJECTED: url must be a nonempty string")
    if len(url) > MAX_URL_LENGTH:
        raise URLRejected(f"URL_REJECTED: url is longer than {MAX_URL_LENGTH} characters")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url):
        raise URLRejected("URL_REJECTED: url contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise URLRejected(f"URL_REJECTED: malformed url ({error})") from error
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        raise URLRejected("URL_REJECTED: only http and https URLs can be fetched")
    if parts.username is not None or parts.password is not None:
        raise URLRejected("URL_REJECTED: URLs with a user name or password are not fetched")
    if not parts.hostname:
        raise URLRejected("URL_REJECTED: url has no host")
    host = _ascii_host(parts.hostname)
    port = port if port is not None else DEFAULT_PORTS[scheme]
    if port not in allowed_ports:
        raise URLRejected(f"URL_REJECTED: port {port} is not allowed")

    netloc = f"[{host}]" if ":" in host else host
    if port != DEFAULT_PORTS[scheme]:
        netloc += f":{port}"
    path = quote(parts.path or "/", safe=_PATH_SAFE)
    query = quote(parts.query, safe=_QUERY_SAFE)
    fetch_url = urlunsplit((scheme, netloc, path, query, ""))

    kept = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING.match(key.lower())
    )
    identity_key = urlunsplit((scheme, netloc, path, urlencode(kept), ""))
    return CheckedURL(url, scheme, host, port, fetch_url, identity_key)
