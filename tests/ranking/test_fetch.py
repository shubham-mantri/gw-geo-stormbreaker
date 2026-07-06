"""Tests for the candidate-sourcing page fetcher (M5, ranking/fetch.py).

Two halves, mirroring `measurement/parse.py`'s split:

- `HttpxPageFetcher` is the real, live-HTTP implementation. It is exercised here ONLY through
  `respx` (an in-process transport mock) -- never a real network call -- so these tests stay
  hermetic (TRD §12) and run under the default `not live` gate. `respx` is the same tool the
  content-originality corpus-search tests use for the same purpose.
- `FetchedPage`/`PageFetcher` are the injected seam every downstream consumer (`sourcing.py`)
  depends on, so those consumers can use a trivial dict-backed fake instead of any HTTP at all.
"""

from __future__ import annotations

import ipaddress

import httpx
import respx

from gw_geo.ranking.fetch import FetchedPage, HttpxPageFetcher

_URL = "https://example.com/post"
_PUBLIC_IP = "93.184.216.34"


def _fake_resolver(host: str) -> list[str]:
    """Hermetic DNS stub for the SSRF guard: never touches the network (TRD §12).

    An IP literal resolves to itself (so loopback/private/link-local literals stay blocked); any DNS
    name (e.g. ``example.com``) resolves to a fixed public IP so a normal public URL is allowed.
    """
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return [_PUBLIC_IP]
    return [host]


def _fetcher(**kwargs):
    """`HttpxPageFetcher` with the hermetic fake resolver injected (no real DNS under `not live`)."""
    kwargs.setdefault("resolver", _fake_resolver)
    return HttpxPageFetcher(**kwargs)


def test_fetched_page_defaults() -> None:
    page = FetchedPage(text="hello")
    assert page.text == "hello"
    assert page.published_at is None


@respx.mock
def test_fetch_extracts_visible_text_and_drops_scripts() -> None:
    html = (
        "<html><head><style>.x{color:red}</style>"
        '<script>var secret = 1;</script></head>'
        "<body><h1>Best CRM</h1><p>Acme is a great CRM.</p>"
        "<script>tracker();</script></body></html>"
    )
    respx.get(_URL).mock(return_value=httpx.Response(200, html=html))

    page = _fetcher().fetch(_URL)

    assert page is not None
    assert "Best CRM" in page.text and "Acme is a great CRM." in page.text
    # Script/style bodies must never leak into the "visible text".
    assert "secret" not in page.text and "tracker" not in page.text
    assert "color:red" not in page.text


@respx.mock
def test_fetch_parses_json_ld_date_published() -> None:
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type": "Article", "datePublished": "2024-03-01T09:30:00Z"}'
        "</script></head><body><p>Body text</p></body></html>"
    )
    respx.get(_URL).mock(return_value=httpx.Response(200, html=html))

    page = _fetcher().fetch(_URL)

    assert page is not None
    assert page.published_at == "2024-03-01"  # normalized to a freshness_days-parseable ISO date


@respx.mock
def test_fetch_parses_json_ld_nested_in_graph() -> None:
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@context": "https://schema.org", "@graph": '
        '[{"@type": "WebPage"}, {"@type": "Article", "datePublished": "2022-11-15"}]}'
        "</script></head><body><p>x</p></body></html>"
    )
    respx.get(_URL).mock(return_value=httpx.Response(200, html=html))

    page = _fetcher().fetch(_URL)
    assert page is not None
    assert page.published_at == "2022-11-15"


@respx.mock
def test_fetch_parses_meta_published_time_when_no_json_ld() -> None:
    html = (
        "<html><head>"
        '<meta property="article:published_time" content="2023-05-05T12:00:00+00:00">'
        "</head><body><p>x</p></body></html>"
    )
    respx.get(_URL).mock(return_value=httpx.Response(200, html=html))

    page = _fetcher().fetch(_URL)
    assert page is not None
    assert page.published_at == "2023-05-05"


@respx.mock
def test_fetch_no_date_returns_none_published_at() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(200, html="<p>no date here</p>"))
    page = _fetcher().fetch(_URL)
    assert page is not None
    assert page.published_at is None


@respx.mock
def test_fetch_unparseable_date_degrades_to_none() -> None:
    # A garbage datePublished must never crash freshness_days downstream -- it degrades to None.
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type": "Article", "datePublished": "last Tuesday"}'
        "</script></head><body><p>x</p></body></html>"
    )
    respx.get(_URL).mock(return_value=httpx.Response(200, html=html))
    page = _fetcher().fetch(_URL)
    assert page is not None
    assert page.published_at is None


@respx.mock
def test_fetch_non_2xx_returns_none() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(404, html="not found"))
    assert _fetcher().fetch(_URL) is None


@respx.mock
def test_fetch_timeout_returns_none() -> None:
    respx.get(_URL).mock(side_effect=httpx.TimeoutException("slow"))
    assert _fetcher().fetch(_URL) is None


@respx.mock
def test_fetch_transport_error_returns_none() -> None:
    respx.get(_URL).mock(side_effect=httpx.ConnectError("refused"))
    assert _fetcher().fetch(_URL) is None


@respx.mock
def test_fetch_follows_redirects_and_sets_user_agent() -> None:
    # 301 -> 200 chain: follow_redirects must be on, and our custom UA must ride along.
    respx.get(_URL).mock(
        return_value=httpx.Response(301, headers={"Location": "https://example.com/final"})
    )
    final = respx.get("https://example.com/final").mock(
        return_value=httpx.Response(200, html="<p>final page</p>")
    )
    page = _fetcher(user_agent="gw-geo-test/1.0").fetch(_URL)
    assert page is not None and "final page" in page.text
    assert final.calls.last.request.headers["User-Agent"] == "gw-geo-test/1.0"


# --- SSRF guard (M5 review): reject internal/non-public targets, never issue the request ---------


@respx.mock
def test_fetch_rejects_loopback_url() -> None:
    # An authenticated editor pointing a URL at loopback must be refused -- and never even dispatched.
    route = respx.get("http://127.0.0.1/admin").mock(
        return_value=httpx.Response(200, html="<p>secret</p>")
    )
    assert _fetcher().fetch("http://127.0.0.1/admin") is None
    assert not route.called


@respx.mock
def test_fetch_rejects_link_local_cloud_metadata_url() -> None:
    # The canonical SSRF target: the cloud metadata endpoint on the link-local range.
    route = respx.get("http://169.254.169.254/latest/meta-data/").mock(
        return_value=httpx.Response(200, html="<p>iam-creds</p>")
    )
    assert _fetcher().fetch("http://169.254.169.254/latest/meta-data/") is None
    assert not route.called


@respx.mock
def test_fetch_rejects_private_url() -> None:
    route = respx.get("http://10.0.0.5/internal").mock(
        return_value=httpx.Response(200, html="<p>x</p>")
    )
    assert _fetcher().fetch("http://10.0.0.5/internal") is None
    assert not route.called


def test_fetch_rejects_non_http_scheme() -> None:
    # No scheme other than http/https is ever fetched (file://, ftp://, ...); no request is made.
    assert _fetcher().fetch("file:///etc/passwd") is None
    assert _fetcher().fetch("ftp://example.com/x") is None


@respx.mock
def test_fetch_rejects_redirect_to_internal_host() -> None:
    # A *public* URL that 30x-redirects to an internal host is blocked at the hop, never followed.
    respx.get("https://example.com/redirect").mock(
        return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    internal = respx.get("http://169.254.169.254/").mock(
        return_value=httpx.Response(200, html="<p>iam-creds</p>")
    )
    assert _fetcher().fetch("https://example.com/redirect") is None
    assert not internal.called  # the internal redirect target is never fetched


@respx.mock
def test_fetch_allows_normal_public_url() -> None:
    # The public happy path still works (host resolves to a public IP via the injected fake).
    respx.get(_URL).mock(return_value=httpx.Response(200, html="<p>public content</p>"))
    page = _fetcher().fetch(_URL)
    assert page is not None and "public content" in page.text
