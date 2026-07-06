"""Claude engine adapter (TRD §5.2) via the Anthropic Messages API + `web_search` tool.

Calls Anthropic's Messages API (`POST /v1/messages`) with the server-side `web_search` tool
enabled so answers reflect live retrieval, then maps the response onto the shared `ProbeResult`
contract: concatenated assistant text, cited URLs surfaced by the web-search tool, measured
request latency, and an estimated `cost_usd` from token usage.

This is a distinct **probe** adapter (a measured engine surface) from `measurement.parse.
ClaudeExtractor`, which uses Claude internally as a JSON-mode extraction step over an already-
captured answer. The two use the same underlying Messages API but serve different purposes and
are wired independently -- this module does not disturb that extractor.

Import is side-effect-free: this module never calls `measurement.probe.base.register()` itself --
that happens at wiring time (runner/CLI), same convention as the other adapters.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from gw_geo.common.models import ProbeResult

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_MAX_TOKENS = 1024

# USD per 1M tokens, as (input_rate, output_rate). Approximate published Anthropic API pricing,
# kept as a local constant so cost estimation never requires a network call. Update here if
# pricing changes; a model absent from the table falls back to the Sonnet rate below.
_RATE_PER_1M_TOKENS_USD: dict[str, tuple[float, float]] = {
    "claude-opus-4-5": (5.00, 25.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
_DEFAULT_RATE = _RATE_PER_1M_TOKENS_USD["claude-sonnet-4-5"]


def _compute_cost_usd(model: str, usage: dict[str, Any]) -> float:
    """Token-based cost estimate from the Messages API `usage` block."""
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    rate_in, rate_out = _RATE_PER_1M_TOKENS_USD.get(model, _DEFAULT_RATE)
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


def _extract_answer(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Concatenate assistant `text` blocks; collect cited URLs from both citation sources.

    A `web_search`-enabled Messages response interleaves `server_tool_use` (the search query,
    carries no answer text or citations -- skipped), `web_search_tool_result` (every page the
    search returned, as a `content` list of `web_search_result` items with a `url` field), and
    `text` blocks (the assistant's answer, each optionally carrying a `citations` list of
    `web_search_result_location` objects that also have a `url` field). Cited URLs commonly
    appear in both places -- once as a raw search result, again as an inline citation -- so they
    are de-duplicated while preserving first-seen order across the whole `content` array.
    """
    text_parts: list[str] = []
    cited_urls: list[str] = []
    seen_urls: set[str] = set()

    def _add_url(url: str | None) -> None:
        if url and url not in seen_urls:
            seen_urls.add(url)
            cited_urls.append(url)

    for block in payload.get("content", []):
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
            for citation in block.get("citations") or []:
                _add_url(citation.get("url"))
        elif block_type == "web_search_tool_result":
            result_content = block.get("content")
            # A failed search reports its error as a single object, not a list of results --
            # see the Claude API's web-search error shape. Only a list carries citable URLs.
            if isinstance(result_content, list):
                for result in result_content:
                    _add_url(result.get("url"))

    return "".join(text_parts), cited_urls


class ClaudeAdapter:
    """`EngineAdapter` for Anthropic's Messages API with the `web_search` tool enabled."""

    name = "claude"
    supports_citations = True

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        model: str = "claude-sonnet-4-5",
    ) -> None:
        self._api_key = api_key
        self._client = client if client is not None else httpx.AsyncClient(timeout=120.0)
        self._model = model

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Probe the Claude Messages API with the `web_search` tool enabled.

        `geo`/`persona` are accepted for `EngineAdapter` parity with other adapters; the Messages
        API has no geo/persona targeting parameter today, so they aren't sent upstream.
        """
        started = time.perf_counter()
        response = await self._client.post(
            _API_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": _MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}],
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
            },
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        answer_text, cited_urls = _extract_answer(payload)
        cost_usd = _compute_cost_usd(self._model, payload.get("usage", {}))

        return ProbeResult(
            engine=self.name,
            answer_text=answer_text,
            cited_urls=cited_urls,
            raw=payload,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
