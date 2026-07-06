"""Page fetching for the candidate-sourcing crawler (M5): public-URL text + publish date.

The ranking pipeline (`ranking/runner.py`) trains a per-engine model on candidate URLs carrying a
`FeatureVector` (`ranking/features.py`). Those features need the candidate's *content* (for
`structure_score`/`info_density`/`embedding_similarity`/...) and its *publish date* (for
`freshness_days`). This module is the one place that turns a public URL into exactly those two
things, and the only place in the M5 crawler that touches the network.

Two-layer design, mirroring `measurement/parse.py`'s pure/live split and `content.kb`'s injected
client seam:

- `PageFetcher` (Protocol) + `FetchedPage` (the value object) are the seam every consumer
  (`ranking/sourcing.py`) depends on. Consumers never construct a real fetcher themselves -- one is
  **injected**, so hermetic tests pass a trivial dict-backed fake and make no HTTP call at all.
- `HttpxPageFetcher` is the real, live-HTTP implementation. It guards every request against SSRF
  (see below), follows redirects safely, and treats *every* failure mode (timeout, transport
  error, any non-2xx status, a blocked target) as "no page" -- returning `None` rather than raising
  -- so one dead or unsafe link never crashes a crawl. Visible text is extracted with BeautifulSoup
  (script/style/etc. stripped); the publish date is read from JSON-LD `datePublished` (incl. nested
  `@graph`) or a `<meta>` published-time tag, and is normalized to a `freshness_days`-parseable ISO
  date (or dropped) so a garbage date value can never crash feature extraction downstream.

**SSRF guard (M5 review).** The URL a candidate carries is attacker-influenceable: an authenticated
`editor` can set `brand.domain` (or a citation URL) to `http://169.254.169.254/...` (cloud metadata)
or any internal host. So before *every* fetch -- the initial URL and each redirect hop -- the target
is validated (`_is_safe_url`): the scheme must be `http`/`https`, and the host must resolve entirely
to public IPs (every address returned by the injected resolver is rejected if it is
private/loopback/link-local/reserved/multicast/unspecified). Redirects are followed manually
(`follow_redirects=False`) precisely so each hop can be re-validated before it is fetched. A blocked
target is skipped exactly like any other fetch failure (`None`).

LOCAL-ONLY / white-hat: this fetches ordinary public URLs (the ones AI engines already cited) with
an honest User-Agent. It is not a SERP/search API and adds no cloud dependency (PRD NG1).
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import BaseModel

# A plain, honest User-Agent (white-hat, PRD NG1) -- we identify ourselves rather than spoofing a
# browser. Overridable per fetcher instance.
_DEFAULT_USER_AGENT = "gw-geo-stormbreaker/1.0 (+https://gushwork.ai; GEO content crawler)"
_DEFAULT_TIMEOUT_S = 10.0

# SSRF guard (M5 review). Only these schemes are ever fetched; redirects are followed manually up to
# this many hops (each hop re-validated), so a public URL cannot 30x its way to an internal host.
_ALLOWED_SCHEMES = frozenset({"http", "https"})
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_DEFAULT_MAX_REDIRECTS = 5

# HTML nodes whose text is never "visible" page content -- stripped before `get_text`.
_NON_CONTENT_TAGS = ("script", "style", "noscript", "template", "head")

# `<meta>` published-date carriers, in priority order (OpenGraph article time first, then the
# schema.org itemprop/name variants). Each is a `(attr, value)` selector matched case-insensitively.
_META_DATE_SELECTORS: tuple[tuple[str, str], ...] = (
    ("property", "article:published_time"),
    ("name", "article:published_time"),
    ("itemprop", "datePublished"),
    ("name", "datePublished"),
    ("property", "og:published_time"),
    ("name", "date"),
)


class FetchedPage(BaseModel):
    """The two things feature extraction needs from a candidate URL.

    `text` is the page's visible text (script/style stripped); `published_at` is an ISO date
    string (`YYYY-MM-DD`) when a publish date could be read and parsed, else `None`. Deliberately
    minimal -- everything else a `FeatureVector` needs is derived from these two by
    `ranking.features.extract_features`.
    """

    text: str
    published_at: str | None = None


class PageFetcher(Protocol):
    """Anything that can turn a public URL into a `FetchedPage` (or `None` if it can't be fetched).

    The injected seam for the candidate-sourcing crawler: `HttpxPageFetcher` is the real
    implementation; hermetic tests inject a dict-backed fake so `sourcing.py` never makes a live
    HTTP call (TRD §12).
    """

    def fetch(self, url: str) -> FetchedPage | None: ...


def _safe_iso_date(value: str | None) -> str | None:
    """Normalize a raw date string to a `YYYY-MM-DD` ISO date, or `None` if it can't be parsed.

    Mirrors `ranking.features._parse_date` (date-only or full ISO-8601, `Z`-suffix tolerated) but is
    *total*: an unparseable value (`"last Tuesday"`, an empty string, a non-ISO format) yields
    `None` rather than raising. This is the guarantee that lets the fetcher hand `published_at`
    straight to `freshness_days` -- which does raise on a bad string -- without ever crashing a crawl.
    """
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _visible_text(soup: BeautifulSoup) -> str:
    """Extract the page's visible text: strip non-content nodes, then collapse whitespace.

    Note (documented limitation, M5): this returns *visible text*, so HTML-tag-shaped feature
    signals (`has_schema`'s `application/ld+json`, `<table>`, `<h1>`) are not preserved -- the
    text-level features (`info_density`, `embedding_similarity`, `freshness_days`,
    `domain_authority`, `corroboration_count`) carry the signal for a crawled candidate.
    """
    for tag in soup(list(_NON_CONTENT_TAGS)):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def _find_date_published(obj: Any) -> str | None:
    """Depth-first search a parsed JSON-LD object for the first `datePublished` string value.

    JSON-LD blocks are often a single object, sometimes a list of them, and often wrap the real
    entity inside an `@graph` array -- so recurse through dicts and lists rather than only reading
    the top level.
    """
    if isinstance(obj, dict):
        value = obj.get("datePublished")
        if isinstance(value, str):
            return value
        for nested in obj.values():
            found = _find_date_published(nested)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_date_published(item)
            if found is not None:
                return found
    return None


def _json_ld_date(soup: BeautifulSoup) -> str | None:
    """The first parseable `datePublished` from any `application/ld+json` block, else `None`."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        candidate = _safe_iso_date(_find_date_published(data))
        if candidate is not None:
            return candidate
    return None


def _meta_date(soup: BeautifulSoup) -> str | None:
    """The first parseable published date from a known `<meta>` published-time tag, else `None`."""
    for attr, wanted in _META_DATE_SELECTORS:
        for meta in soup.find_all("meta", attrs={attr: wanted}):
            if not isinstance(meta, Tag):
                continue
            content = meta.get("content")
            candidate = _safe_iso_date(content if isinstance(content, str) else None)
            if candidate is not None:
                return candidate
    return None


def _extract_published_at(soup: BeautifulSoup) -> str | None:
    """Best-effort publish date: JSON-LD first (most reliable), then a `<meta>` tag; else `None`."""
    return _json_ld_date(soup) or _meta_date(soup)


# The DNS-resolution seam for the SSRF guard: a hostname -> the list of IP strings it resolves to.
# Injected so the hermetic suite (`tests/ranking/test_fetch.py`) supplies a fake mapping and never
# touches real DNS/network under `not live`; production uses `_resolve_host` (a `socket.getaddrinfo`
# wrapper). Both a DNS name and an IP literal resolve through the same seam.
Resolver = Callable[[str], list[str]]


def _resolve_host(host: str) -> list[str]:
    """Resolve `host` to every distinct IP `socket.getaddrinfo` returns.

    An IP literal (e.g. ``169.254.169.254`` or ``::1``) resolves to itself without a DNS query; a
    name goes through the system resolver. Raises `OSError` (incl. `socket.gaierror`) on failure --
    `_is_safe_url` treats that as "unresolvable" and blocks the URL (fail-closed).
    """
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    # `info[4]` is the sockaddr; its first element is the IP string (typed str | int by the stdlib
    # stubs for the IPv4/IPv6 union, so coerce to str for the resolver's list[str] contract).
    return list({str(info[4][0]) for info in infos})


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if `ip` targets a non-public range we must never fetch (SSRF defense).

    Blocks private (incl. IPv6 ULA ``fc00::/7``), loopback, link-local (incl. the cloud metadata
    address ``169.254.169.254``), reserved, multicast, and the unspecified address. An IPv4-mapped
    IPv6 address (``::ffff:a.b.c.d``) is unwrapped first so a mapped internal target can't slip past
    the IPv4 range checks.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_safe_url(url: str, *, resolve: Resolver) -> bool:
    """True iff `url` is a fetchable *public* target (the SSRF guard, M5 review).

    Fail-closed on every uncertainty: requires an ``http``/``https`` scheme and a non-empty host,
    resolves that host via the injected `resolve`, and requires **every** resolved IP to be public
    (`_is_blocked_ip` is False for all of them). A malformed URL, a resolution failure, no resolved
    addresses, or an unparseable/blocked IP all return `False`. This is what stops an
    authenticated `editor` pointing `brand.domain`/a citation URL at ``169.254.169.254`` or an
    internal host and having the server fetch it.
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return False
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False
    host = parsed.host
    if not host:
        return False
    try:
        ip_strings = resolve(host)
    except OSError:
        return False
    if not ip_strings:
        return False
    for ip_str in ip_strings:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if _is_blocked_ip(ip):
            return False
    return True


class HttpxPageFetcher:
    """`PageFetcher` backed by `httpx.get`, with an SSRF guard and safe redirect handling.

    Before every request -- the initial URL *and* each redirect hop -- the target is validated by
    `_is_safe_url`: the scheme must be ``http``/``https`` and the host must resolve entirely to
    public IPs, so an attacker-controlled `brand.domain`/citation URL pointing at a private,
    loopback, link-local (incl. ``169.254.169.254``), reserved, multicast, or unspecified address is
    refused. Redirects are followed manually (`follow_redirects=False`) precisely so each hop is
    re-validated before it is fetched -- a public URL that 30x-redirects to an internal host is
    blocked at the hop, never followed.

    Every failure mode -- a blocked/unsafe target, a redirect loop or missing `Location`, a timeout,
    any transport/connection error, or any non-2xx status (`raise_for_status`) -- is caught and
    returned as `None`, so a dead, slow, or unsafe candidate link is simply skipped, never fatal to
    the crawl. The DNS resolver is injected (`resolver=`) so the hermetic suite supplies a fake and
    never touches real DNS/network under `not live`; production uses `socket.getaddrinfo`. Not
    exercised against a real host: tests drive it through `respx` (in-process transport mock) with a
    fake resolver, or inject a `PageFetcher` fake upstream.
    """

    def __init__(
        self,
        *,
        timeout: float = _DEFAULT_TIMEOUT_S,
        user_agent: str = _DEFAULT_USER_AGENT,
        resolver: Resolver | None = None,
        max_redirects: int = _DEFAULT_MAX_REDIRECTS,
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent
        self._resolve = resolver if resolver is not None else _resolve_host
        self._max_redirects = max_redirects

    def _get(self, url: str) -> httpx.Response | None:
        """Fetch `url` SSRF-safely, following redirects manually and re-validating every hop.

        Returns the final 2xx `Response`, or `None` for a blocked target, too many redirects, a
        redirect with no `Location`, any non-2xx status, or any transport error/timeout.
        """
        current = url
        for _ in range(self._max_redirects + 1):
            if not _is_safe_url(current, resolve=self._resolve):
                return None
            try:
                response = httpx.get(
                    current,
                    follow_redirects=False,  # follow manually so each hop is re-validated first
                    timeout=self._timeout,
                    headers={"User-Agent": self._user_agent},
                )
            except httpx.HTTPError:
                return None
            if response.status_code in _REDIRECT_STATUS_CODES:
                location = response.headers.get("Location")
                if not location:
                    return None
                current = str(httpx.URL(current).join(location))  # resolve relative redirects
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPError:
                return None
            return response
        return None  # too many redirect hops -- treat as a fetch failure and skip the candidate

    def fetch(self, url: str) -> FetchedPage | None:
        response = self._get(url)
        if response is None:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        # Read the publish date BEFORE `_visible_text` decomposes <head>/<script>/<meta> -- those
        # are exactly the nodes the date lives in, so stripping them first would lose it.
        published_at = _extract_published_at(soup)
        return FetchedPage(text=_visible_text(soup), published_at=published_at)
