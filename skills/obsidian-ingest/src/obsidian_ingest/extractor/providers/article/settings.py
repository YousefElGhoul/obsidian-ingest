"""The `[articles]` table of the trusted settings file: the network policy for fetching.

It is set only by the person installing the skill, never by a tool argument.
"""

from dataclasses import dataclass

TABLE = "articles"
DEFAULT_PORTS = frozenset({80, 443})


@dataclass(frozen=True, slots=True)
class ArticleSettings:
    """Trusted network policy for fetching web pages."""

    allow_private_network: bool = False
    extra_ports: tuple[int, ...] = ()

    @property
    def allowed_ports(self) -> frozenset[int]:
        return DEFAULT_PORTS | frozenset(self.extra_ports)


def parse_settings(section: object) -> ArticleSettings:
    """Validate the `[articles]` table; an absent table means the safe defaults."""
    if not isinstance(section, dict) or set(section) - {"allow_private_network", "extra_ports"}:
        raise ValueError("[articles] accepts only allow_private_network and extra_ports")
    private = section.get("allow_private_network", False)
    ports = section.get("extra_ports", [])
    if not isinstance(private, bool):
        raise TypeError("articles.allow_private_network must be true or false")
    if not isinstance(ports, list) or any(
        isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535
        for port in ports
    ):
        raise ValueError("articles.extra_ports must be an array of port numbers")
    return ArticleSettings(private, tuple(sorted(set(ports))))
