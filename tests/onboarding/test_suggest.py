"""Tests for domain-first onboarding auto-fill (`gw_geo.onboarding.suggest`).

Hermetic: the page fetcher + LLM client are both injected Protocols, so every test drives a trivial
fake -- **no live HTTP or LLM call is ever made** (mirrors the injected-seam convention in
`tests/ranking/test_fetch.py` / `tests/content/test_generate.py`). The fakes hand back raw markup /
a canned tool-call dict; failure modes (fetcher raises, LLM raises, malformed payloads) assert the
"never raise -- degrade to the domain heuristic / an empty competitor list" guarantee onboarding
depends on.
"""

from __future__ import annotations

from typing import Any

from gw_geo.onboarding.suggest import (
    BrandSuggestion,
    normalize_url,
    suggest_brand_details,
)
from gw_geo.ranking.fetch import FetchedPage


class FakeFetcher:
    """A `PageFetcher` that returns a canned `FetchedPage` (or `None`) and records the fetched URL."""

    def __init__(self, page: FetchedPage | None) -> None:
        self._page = page
        self.fetched_url: str | None = None

    def fetch(self, url: str) -> FetchedPage | None:
        self.fetched_url = url
        return self._page


class RaisingFetcher:
    """A `PageFetcher` whose `fetch` raises -- exercises the "never raise on fetch failure" path."""

    def fetch(self, url: str) -> FetchedPage | None:
        raise RuntimeError("network exploded")


class FakeLLM:
    """An `LLMClient` returning a canned structured dict, and recording the prompt/schema it saw."""

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.seen_prompt: str | None = None
        self.seen_schema: dict[str, Any] | None = None

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.seen_prompt = prompt
        self.seen_schema = schema
        return self._result


class RaisingLLM:
    """An `LLMClient` whose `complete` raises -- exercises the "empty competitors, never raise" path."""

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raise RuntimeError("llm exploded")


_NO_COMPETITORS = FakeLLM({"competitors": []})


def _named_llm(name: str) -> FakeLLM:
    """A `FakeLLM` that returns a fixed brand ``name`` and no competitors."""
    return FakeLLM({"name": name, "competitors": []})


# --- normalize_url ---------------------------------------------------------------------------


def test_normalize_url_adds_https_scheme() -> None:
    assert normalize_url("acme.com") == "https://acme.com"
    assert normalize_url("  acme.com  ") == "https://acme.com"


def test_normalize_url_keeps_existing_scheme() -> None:
    assert normalize_url("http://acme.com") == "http://acme.com"
    assert normalize_url("https://acme.com/x") == "https://acme.com/x"


# --- brand name: from the LLM (domain + page hint), domain heuristic only when the name is empty --


def test_llm_name_is_used_verbatim() -> None:
    # The LLM derives the name from the domain; it wins over the domain heuristic.
    fetcher = FakeFetcher(FetchedPage(text="body text"))
    out = suggest_brand_details(domain="acme.com", fetcher=fetcher, llm=_named_llm("Acme Robotics"))
    assert out.name == "Acme Robotics"
    assert fetcher.fetched_url == "https://acme.com"  # fetched the normalized URL (for the hint)


def test_jsonld_name_passed_as_hint_and_beats_og_and_title() -> None:
    html = """
    <html><head>
      <title>Home | Some Boilerplate</title>
      <meta property="og:site_name" content="OG Name">
      <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Organization","name":"Acme Robotics"}
      </script>
    </head><body>hi</body></html>
    """
    llm = _named_llm("Acme Robotics Inc")
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(FetchedPage(text=html)), llm=llm)
    assert out.name == "Acme Robotics Inc"                # name comes from the LLM
    assert "Acme Robotics" in (llm.seen_prompt or "")     # JSON-LD name fed as the hint (beats og/title)


def test_jsonld_nested_graph_website_passed_as_hint() -> None:
    html = '<script type="application/ld+json">{"@graph":[{"@type":"WebSite","name":"Graph Site"}]}</script>'
    llm = _named_llm("Graph Site")
    suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(FetchedPage(text=html)), llm=llm)
    assert "Graph Site" in (llm.seen_prompt or "")


def test_og_site_name_used_as_hint_when_no_jsonld() -> None:
    html = '<head><meta property="og:site_name" content="Acme OG"><title>Acme | CRM</title></head>'
    llm = _named_llm("Acme")
    suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(FetchedPage(text=html)), llm=llm)
    assert "Acme OG" in (llm.seen_prompt or "")  # og:site_name beats <title> for the hint


def test_title_hint_strips_trailing_boilerplate() -> None:
    # The <title> hint has its " | tagline"/" - …" boilerplate stripped before it reaches the prompt.
    for title, expected_hint, boilerplate in [
        ("Acme | The best CRM for startups", "Acme", "The best CRM"),
        ("Acme – Sales software", "Acme", "Sales software"),  # en dash
    ]:
        html = f"<head><title>{title}</title></head>"
        llm = _named_llm("Acme")
        suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(FetchedPage(text=html)), llm=llm)
        prompt = llm.seen_prompt or ""
        assert f"'{expected_hint}'" in prompt and boilerplate not in prompt, title


def test_visible_text_snippet_used_as_hint_when_no_markup() -> None:
    # The real HttpxPageFetcher returns visible text only -> a bounded snippet becomes the hint.
    llm = _named_llm("Acme")
    suggest_brand_details(
        domain="acme.com",
        fetcher=FakeFetcher(FetchedPage(text="Welcome to Acme, the CRM built for teams")),
        llm=llm,
    )
    assert "Welcome to Acme" in (llm.seen_prompt or "")


def test_no_page_hint_when_fetch_returns_none() -> None:
    llm = _named_llm("Acme")
    suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)
    prompt = llm.seen_prompt or ""
    assert "acme.com" in prompt          # the domain always drives the prompt
    assert "Hint read off" not in prompt  # no page -> no hint line


def test_name_falls_back_to_domain_when_llm_name_empty() -> None:
    # LLM returns no name (visible-text page, no markup) -> the domain heuristic fills it in.
    out = suggest_brand_details(
        domain="acme.com",
        fetcher=FakeFetcher(FetchedPage(text="just some visible body text, no head")),
        llm=_NO_COMPETITORS,
    )
    assert out.name == "Acme"


def test_domain_heuristic_strips_www_and_tld_and_splits() -> None:
    # When the LLM returns no name, the domain heuristic is the fallback.
    cases = {
        "acme.com": "Acme",
        "https://www.acme.com/": "Acme",
        "foo-bar.io": "Foo Bar",
        "WWW.Globex.CO": "Globex",
    }
    for domain, expected in cases.items():
        out = suggest_brand_details(
            domain=domain, fetcher=FakeFetcher(None), llm=_NO_COMPETITORS
        )
        assert out.name == expected, domain


def test_fetch_failure_never_raises_and_uses_domain() -> None:
    # fetcher.fetch raising must not propagate -- onboarding still works (hint dropped, heuristic name).
    out = suggest_brand_details(domain="acme.com", fetcher=RaisingFetcher(), llm=_NO_COMPETITORS)
    assert out.name == "Acme"
    assert out.competitors == []


def test_llm_failure_falls_back_to_domain_name() -> None:
    # LLM raising -> no name, no competitors -> domain heuristic name, empty competitors, no 5xx.
    out = suggest_brand_details(domain="globex.com", fetcher=FakeFetcher(None), llm=RaisingLLM())
    assert out.name == "Globex"
    assert out.competitors == []


# --- competitors -----------------------------------------------------------------------------


def test_competitors_parsed_to_name_list() -> None:
    llm = FakeLLM(
        {"name": "Acme", "competitors": [{"name": "Beta", "domain": "beta.com"}, {"name": "Gamma"}]}
    )
    out = suggest_brand_details(
        domain="acme.com", fetcher=FakeFetcher(None), llm=llm
    )
    assert out.competitors == ["Beta", "Gamma"]  # names only, list[str]
    # The LLM was actually prompted with a structured-output schema (tool-call pattern) for the domain.
    assert llm.seen_schema is not None
    assert "acme.com" in (llm.seen_prompt or "")


def test_competitors_dedupe_cap_and_exclude_self() -> None:
    llm = FakeLLM(
        {
            "competitors": [
                {"name": "Beta"},
                {"name": "beta"},  # dupe (case-insensitive)
                {"name": "Acme"},  # the brand itself -> excluded
                {"name": "C1"},
                {"name": "C2"},
                {"name": "C3"},
                {"name": "C4"},
                {"name": "C5"},
                {"name": "C6"},  # would exceed the ~6 cap
            ]
        }
    )
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=llm)
    assert "acme" not in [c.lower() for c in out.competitors]
    assert out.competitors == ["Beta", "C1", "C2", "C3", "C4", "C5"]  # deduped, self-excluded, ≤6


def test_competitors_accepts_plain_string_items() -> None:
    out = suggest_brand_details(
        domain="acme.com", fetcher=FakeFetcher(None), llm=FakeLLM({"competitors": ["Beta", "Gamma"]})
    )
    assert out.competitors == ["Beta", "Gamma"]


def test_competitors_llm_failure_returns_empty() -> None:
    out = suggest_brand_details(domain="acme.com", fetcher=FakeFetcher(None), llm=RaisingLLM())
    assert out.competitors == []  # never raises; onboarding still works with manual entry


def test_competitors_malformed_payload_returns_empty() -> None:
    for bad in ({}, {"competitors": "nope"}, {"competitors": [1, 2, 3]}, {"other": []}):
        out = suggest_brand_details(
            domain="acme.com", fetcher=FakeFetcher(None), llm=FakeLLM(bad)
        )
        assert out.competitors == [], bad


# --- BrandSuggestion shape -------------------------------------------------------------------


def test_brand_suggestion_shape() -> None:
    out = suggest_brand_details(
        domain="acme.com",
        fetcher=FakeFetcher(FetchedPage(text="<head><title>Acme</title></head>")),
        llm=FakeLLM({"competitors": [{"name": "Beta"}]}),
    )
    assert isinstance(out, BrandSuggestion)
    assert out.model_dump() == {"name": "Acme", "domain": "acme.com", "competitors": ["Beta"]}
