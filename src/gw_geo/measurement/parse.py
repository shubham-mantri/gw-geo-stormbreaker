"""Parse: turn a raw `ProbeResult` into a structured `AnswerExtraction`.

Two independently-testable halves, per `docs/trd.md` §5.3:

- Pure-Python URL handling (`normalize_url`, `domain_of`, `classify_source`) — no I/O, fully
  deterministic, unit-tested directly.
- LLM-backed mention/sentiment extraction behind the `Extractor` protocol, injected into
  `parse()` so tests never make a live call (see `StubExtractor` in the test suite). A real
  Claude JSON-mode implementation (`ClaudeExtractor`) lives here too, but is exercised only via
  manual/integration use, never by the hermetic unit tests.
"""

from __future__ import annotations

import os
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from gw_geo.common.models import AnswerExtraction, Brand, ProbeResult, Sentiment, SourceType

# --------------------------------------------------------------------------------------------
# URL normalization / classification (pure, no I/O)
# --------------------------------------------------------------------------------------------

_UTM_PREFIX = "utm_"

# Domain heuristics for `classify_source`. Only domain-derivable categories are covered here;
# content-shape categories from the TRD taxonomy (e.g. `listicle`) require the answer text
# itself and are out of scope for a URL-only classifier.
_REVIEW_SITE_DOMAINS = {"g2.com", "capterra.com", "trustradius.com", "softwareadvice.com"}
_FORUM_QA_DOMAINS = {"quora.com", "stackoverflow.com", "stackexchange.com"}
_SOCIAL_DOMAINS = {
    "twitter.com",
    "x.com",
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "youtube.com",
    "tiktok.com",
}
_NEWS_PR_DOMAINS = {"prnewswire.com", "businesswire.com", "globenewswire.com"}
_DOCS_HOST_PREFIXES = ("docs.", "developer.", "developers.")


def normalize_url(url: str) -> str:
    """Strip `utm_*` query params and the fragment, lowercase the host, drop trailing slash.

    Non-tracking query params are preserved (order-stable).
    """
    parts = urlsplit(url)
    netloc = parts.netloc.lower()

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept_params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith(_UTM_PREFIX)
    ]
    query = urlencode(kept_params)

    return urlunsplit((parts.scheme, netloc, path, query, ""))


def domain_of(url: str) -> str:
    """Return the registrable host for `url`, lowercased, with a leading `www.` dropped.

    Accepts either a full URL (`https://www.foo.com/a`) or a bare host (`foo.com`), since this
    is also used to resolve a `Brand.domain` value that has no scheme.
    """
    parts = urlsplit(url)
    host = parts.netloc or parts.path
    host = host.lower()
    host = host.split("@")[-1]  # drop userinfo, if any
    host = host.split("/")[0]  # drop any accidental path (bare-host case)
    host = host.split(":")[0]  # drop port
    if host.startswith("www."):
        host = host[len("www.") :]
    return host


def _is_domain_or_subdomain(domain: str, base: str) -> bool:
    return domain == base or domain.endswith(f".{base}")


def classify_source(url: str) -> SourceType:
    """Classify a citation URL by domain.

    This function has no knowledge of any particular brand, so it never returns `OWN_SITE` —
    that classification is applied in `parse()`, where the brand's domain is known.
    """
    domain = domain_of(url)

    if _is_domain_or_subdomain(domain, "reddit.com"):
        return SourceType.REDDIT
    if _is_domain_or_subdomain(domain, "wikipedia.org"):
        return SourceType.WIKIPEDIA
    if any(_is_domain_or_subdomain(domain, base) for base in _REVIEW_SITE_DOMAINS):
        return SourceType.REVIEW_SITE
    if any(_is_domain_or_subdomain(domain, base) for base in _FORUM_QA_DOMAINS):
        return SourceType.FORUM_QA
    if any(_is_domain_or_subdomain(domain, base) for base in _SOCIAL_DOMAINS):
        return SourceType.SOCIAL
    if any(_is_domain_or_subdomain(domain, base) for base in _NEWS_PR_DOMAINS):
        return SourceType.NEWS_PR
    if domain.startswith(_DOCS_HOST_PREFIXES):
        return SourceType.DOCS
    return SourceType.OTHER


def _classify_for_brand(url: str, own_domain: str) -> SourceType:
    """Like `classify_source`, but tags the brand's own domain (or subdomains) as OWN_SITE."""
    if own_domain and _is_domain_or_subdomain(domain_of(url), own_domain):
        return SourceType.OWN_SITE
    return classify_source(url)


# --------------------------------------------------------------------------------------------
# LLM-backed extraction
# --------------------------------------------------------------------------------------------


class Extractor(Protocol):
    def extract(self, answer_text: str, brand: Brand) -> dict[str, Any]:
        """Return {"brand_mentioned": bool, "position": int | None, "sentiment": str,
        "competitors_present": list[str]}.
        """
        ...


class ClaudeExtractor:
    """`Extractor` backed by the Claude Messages API in tool-use (JSON-mode) mode.

    Never called by the unit test suite (tests inject `StubExtractor` per `docs/trd.md` §12 —
    no live LLM calls in CI); this is the real implementation used by the runner. Uses `httpx`
    directly (no `anthropic` SDK dependency) against the documented Messages API.
    """

    _API_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"
    _DEFAULT_MODEL = "claude-opus-4-8"
    _TOOL_NAME = "record_extraction"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.environ.get("GEO_ANTHROPIC_API_KEY", "")
        )
        self._model = model or self._DEFAULT_MODEL
        self._timeout = timeout

    def extract(self, answer_text: str, brand: Brand) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError(
                "ClaudeExtractor requires an Anthropic API key "
                "(pass api_key= or set GEO_ANTHROPIC_API_KEY)."
            )

        response = httpx.post(
            self._API_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self._API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": 1024,
                "tools": [self._tool_schema()],
                "tool_choice": {"type": "tool", "name": self._TOOL_NAME},
                "messages": [{"role": "user", "content": self._prompt(answer_text, brand)}],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()

        for block in payload.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == self._TOOL_NAME:
                extraction: dict[str, Any] = block["input"]
                return extraction

        raise ValueError("Claude response did not include the expected tool_use extraction block.")

    def _tool_schema(self) -> dict[str, Any]:
        return {
            "name": self._TOOL_NAME,
            "description": "Record the structured extraction for an AI-engine answer.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "brand_mentioned": {"type": "boolean"},
                    "position": {
                        "type": ["integer", "null"],
                        "description": "1-indexed rank among named options, null if absent.",
                    },
                    "sentiment": {
                        "type": "string",
                        "enum": [s.value for s in Sentiment],
                    },
                    "competitors_present": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["brand_mentioned", "position", "sentiment", "competitors_present"],
            },
        }

    @staticmethod
    def _prompt(answer_text: str, brand: Brand) -> str:
        competitors = ", ".join(brand.competitors) or "none listed"
        return (
            f"Brand: {brand.name} ({brand.domain}). Known competitors: {competitors}.\n\n"
            f"Answer text from an AI search engine:\n---\n{answer_text}\n---\n\n"
            "Using the record_extraction tool, report whether the brand is mentioned, its "
            "1-indexed rank position among named options (null if not applicable/absent), the "
            "overall sentiment toward the brand (positive, neutral, negative, or comparison if "
            "it's mainly a head-to-head comparison), and which of its competitors are present."
        )


# --------------------------------------------------------------------------------------------
# parse()
# --------------------------------------------------------------------------------------------


def parse(
    result: ProbeResult,
    brand: Brand,
    extractor: Extractor,
    probe_run_id: str,
) -> AnswerExtraction:
    """Combine the injected `Extractor`'s output with normalized/classified citations."""
    extraction = extractor.extract(result.answer_text, brand)

    own_domain = domain_of(brand.domain)
    normalized_urls = [normalize_url(url) for url in result.cited_urls]
    source_types = [_classify_for_brand(url, own_domain) for url in normalized_urls]

    return AnswerExtraction(
        probe_run_id=probe_run_id,
        brand_mentioned=extraction["brand_mentioned"],
        position=extraction["position"],
        sentiment=Sentiment(extraction["sentiment"]),
        cited_urls=normalized_urls,
        source_types=source_types,
        competitors_present=extraction["competitors_present"],
    )
