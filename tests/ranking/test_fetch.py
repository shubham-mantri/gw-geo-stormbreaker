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

import httpx
import respx

from gw_geo.ranking.fetch import FetchedPage, HttpxPageFetcher

_URL = "https://example.com/post"


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

    page = HttpxPageFetcher().fetch(_URL)

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

    page = HttpxPageFetcher().fetch(_URL)

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

    page = HttpxPageFetcher().fetch(_URL)
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

    page = HttpxPageFetcher().fetch(_URL)
    assert page is not None
    assert page.published_at == "2023-05-05"


@respx.mock
def test_fetch_no_date_returns_none_published_at() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(200, html="<p>no date here</p>"))
    page = HttpxPageFetcher().fetch(_URL)
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
    page = HttpxPageFetcher().fetch(_URL)
    assert page is not None
    assert page.published_at is None


@respx.mock
def test_fetch_non_2xx_returns_none() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(404, html="not found"))
    assert HttpxPageFetcher().fetch(_URL) is None


@respx.mock
def test_fetch_timeout_returns_none() -> None:
    respx.get(_URL).mock(side_effect=httpx.TimeoutException("slow"))
    assert HttpxPageFetcher().fetch(_URL) is None


@respx.mock
def test_fetch_transport_error_returns_none() -> None:
    respx.get(_URL).mock(side_effect=httpx.ConnectError("refused"))
    assert HttpxPageFetcher().fetch(_URL) is None


@respx.mock
def test_fetch_follows_redirects_and_sets_user_agent() -> None:
    # 301 -> 200 chain: follow_redirects must be on, and our custom UA must ride along.
    respx.get(_URL).mock(
        return_value=httpx.Response(301, headers={"Location": "https://example.com/final"})
    )
    final = respx.get("https://example.com/final").mock(
        return_value=httpx.Response(200, html="<p>final page</p>")
    )
    page = HttpxPageFetcher(user_agent="gw-geo-test/1.0").fetch(_URL)
    assert page is not None and "final page" in page.text
    assert final.calls.last.request.headers["User-Agent"] == "gw-geo-test/1.0"
