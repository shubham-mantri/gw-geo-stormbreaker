"""Google Gemini engine adapter (TRD §5.2) via the Generative Language API's `generateContent`
endpoint, with the built-in `google_search` grounding tool enabled so answers reflect live
retrieval and carry grounding citations (mirrors the M0 Perplexity/OpenAI adapters -- TRD §3:
monitor consumer-facing behavior, not the static model). Import is side-effect-free: this module
never calls `measurement.probe.base.register()` itself; that happens at wiring time
(`build_runtime`, T18) when `gemini_api_key` is set.
"""

import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from gw_geo.common.models import ProbeResult

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# USD per 1M tokens, as (input_rate, output_rate). Approximate published Gemini API pricing, kept
# as a local constant so cost estimation never requires a network call. Update here if pricing
# changes; a model absent from the table falls back to the gemini-2.5-flash rate.
_RATE_PER_1M_TOKENS_USD: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}
_DEFAULT_RATE = _RATE_PER_1M_TOKENS_USD["gemini-2.5-flash"]


def _compute_cost_usd(model: str, usage: dict[str, Any]) -> float:
    """Token-based cost estimate from the `generateContent` `usageMetadata` block."""
    prompt_tokens = int(usage.get("promptTokenCount", 0))
    completion_tokens = int(usage.get("candidatesTokenCount", 0))
    rate_in, rate_out = _RATE_PER_1M_TOKENS_USD.get(model, _DEFAULT_RATE)
    return (prompt_tokens * rate_in + completion_tokens * rate_out) / 1_000_000


# Real Generative-Language grounding responses never return the source URL directly: every
# `groundingChunks[*].web.uri` is an opaque Vertex *redirect* on this host, and the actual source
# domain is carried in `web.title` (typically a bare host like `reddit.com`). Recovering the title
# is what keeps `classify_source` from tagging every live Gemini citation as `other`.
_VERTEX_REDIRECT_HOST = "vertexaisearch.cloud.google.com"


def _citation_url(chunk: dict[str, Any]) -> str | None:
    """Resolve one grounding chunk to an http(s) citation URL, or `None` if it has no usable one.

    A normal `http(s)` `web.uri` is returned unchanged. When `web.uri` points at the Vertex
    grounding-redirect host (the live case), that redirect is opaque, so the citation is derived
    from `web.title` -- the real source -- instead: a bare host like `reddit.com` is normalized to
    `https://reddit.com`, while a title that is already a full URL is used as-is.
    """
    web = chunk.get("web", {})
    uri = web.get("uri")
    if not isinstance(uri, str) or not uri:
        return None

    if urlsplit(uri).netloc.lower() != _VERTEX_REDIRECT_HOST:
        # A direct source URL -- keep it only if it's http(s) (cited_urls are always http(s)).
        return uri if uri.startswith(("http://", "https://")) else None

    # Vertex redirect -> recover the real source from the title.
    title = web.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    title = title.strip()
    return title if title.startswith(("http://", "https://")) else f"https://{title}"


def _extract_answer(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Concatenate answer text across `content.parts[*].text`; collect grounding citation URLs.

    Citation URLs are resolved from `groundingMetadata.groundingChunks[*]` on the first candidate
    via `_citation_url`, which unwraps Vertex grounding-redirect URIs to their real source domain.
    The same source may back more than one grounding chunk -- e.g. supporting several claims in the
    answer -- so URLs are de-duplicated while preserving first-seen order.
    """
    candidates = payload.get("candidates", [])
    if not candidates:
        return "", []

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    text_parts = [part.get("text", "") for part in parts if "text" in part]

    cited_urls: list[str] = []
    seen_urls: set[str] = set()
    grounding_chunks = candidate.get("groundingMetadata", {}).get("groundingChunks", [])
    for chunk in grounding_chunks:
        url = _citation_url(chunk)
        if url and url not in seen_urls:
            seen_urls.add(url)
            cited_urls.append(url)

    return "".join(text_parts), cited_urls


class GeminiAdapter:
    """`EngineAdapter` for Google's Generative Language API with `google_search` grounding."""

    name = "gemini"
    supports_citations = True

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        model: str = "gemini-2.5-flash",
    ) -> None:
        self._api_key = api_key
        self._client = client if client is not None else httpx.AsyncClient(timeout=120.0)
        self._model = model

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Probe Gemini's `generateContent` endpoint with `google_search` grounding enabled.

        `geo`/`persona` are accepted for `EngineAdapter` parity with other adapters; the request
        does not yet vary by geo or persona (tracked as a later refinement, same as OpenAI/
        Perplexity today).
        """
        started = time.perf_counter()
        response = await self._client.post(
            f"{_API_BASE}/{self._model}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
            },
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        answer_text, cited_urls = _extract_answer(payload)
        cost_usd = _compute_cost_usd(self._model, payload.get("usageMetadata", {}))

        return ProbeResult(
            engine=self.name,
            answer_text=answer_text,
            cited_urls=cited_urls,
            raw=payload,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
