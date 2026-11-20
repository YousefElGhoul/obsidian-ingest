"""Offline web for article tests: a fake resolver, transport and clock, plus page builders."""

import gzip
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from email.message import Message

from obsidian_ingest.extractor import http
from obsidian_ingest.extractor.providers.article import provider

PUBLIC_IP = "93.184.216.34"


def page(body: str, head: str = "", lang: str = "en") -> str:
    """A complete HTML document around an article body."""
    return f"<!doctype html><html lang='{lang}'><head>{head}</head><body>{body}</body></html>"


def words(count: int, word: str = "word") -> str:
    return " ".join(f"{word}{index}" for index in range(count))


def article(paragraphs: int = 6, extra: str = "", title: str = "Retry budgets") -> str:
    """A plain article with headings and enough prose to pass quality checks."""
    body = "".join(
        f"<p>Paragraph {index} explains retry budgets with several plain sentences. "
        f"{words(20, f'p{index}w')}.</p>"
        for index in range(paragraphs)
    )
    return page(
        f"<article><h1>{title}</h1>{body}<h2>Why it matters</h2>"
        f"<p>Retries amplify load during an outage. {words(30, 'why')}.</p>{extra}</article>",
        head=f"<title>{title} | Example</title>",
    )


def json_ld(**fields: object) -> str:
    data = {"@context": "https://schema.org", "@type": "Article"} | fields
    return f"<script type='application/ld+json'>{json.dumps(data)}</script>"


@dataclass
class Reply:
    """One canned response; ``drip`` seconds pass on the fake clock per body chunk."""

    body: bytes | str = b""
    status: int = 200
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    drip: float = 0.0
    chunk: int = 64 * 1024


class _Response:
    def __init__(self, reply: Reply, clock: "FakeClock") -> None:
        self.status = reply.status
        self.headers = Message()
        if reply.content_type:
            self.headers["content-type"] = reply.content_type
        for key, value in reply.headers.items():
            self.headers[key] = value
        body = reply.body.encode() if isinstance(reply.body, str) else reply.body
        self._chunks = [body[i : i + reply.chunk] for i in range(0, len(body), reply.chunk)]
        self._drip = reply.drip
        self._clock = clock

    def read(self, size: int, timeout: float) -> bytes:
        if not self._chunks:
            return b""
        self._clock.now += self._drip
        chunk = self._chunks[0]
        if len(chunk) <= size:
            return self._chunks.pop(0)
        self._chunks[0] = chunk[size:]
        return chunk[:size]

    def close(self) -> None:
        pass


@dataclass
class FakeClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class FakeWeb:
    """Routes by fetch URL; records every request with the address it was sent to."""

    routes: dict[str, Reply | Callable[[], Reply]] = field(default_factory=dict)
    dns: dict[str, list[str]] = field(default_factory=dict)
    clock: FakeClock = field(default_factory=FakeClock)
    requests: list[tuple[str, str]] = field(default_factory=list)
    resolutions: list[str] = field(default_factory=list)
    latency: float = 0.0

    def resolve(self, host: str, port: int) -> list[str]:
        self.resolutions.append(host)
        return self.dns.get(host, [PUBLIC_IP])

    def open(self, target, address: str, headers: dict[str, str], timeout: float):
        self.requests.append((target.fetch_url, address))
        self.clock.now += self.latency
        route = self.routes.get(target.fetch_url)
        if route is None:
            return _Response(Reply(b"missing", status=404), self.clock)
        return _Response(route() if callable(route) else route, self.clock)

    def install(self, monkeypatch, **fetcher_options) -> "FakeWeb":
        monkeypatch.setattr(
            provider,
            "make_fetcher",
            lambda: http.Fetcher(
                resolver=self.resolve, transport=self, sleep=self._sleep, **fetcher_options
            ),
        )
        monkeypatch.setattr(provider, "make_deadline", lambda: http.Deadline(clock=self.clock))
        return self

    def _sleep(self, seconds: float) -> None:
        self.clock.now += seconds


def gzipped(text: str) -> bytes:
    return gzip.compress(text.encode())
