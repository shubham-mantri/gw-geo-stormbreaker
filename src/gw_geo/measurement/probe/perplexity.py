"""Perplexity Sonar engine adapter (TRD §5.2).

Calls Perplexity's chat-completions API (`POST /chat/completions`) and maps the response onto
the shared `ProbeResult` contract: the assistant's answer text, the `citations` URLs Perplexity
returns natively (no extraction needed -- Sonar is the highest-signal starting engine for this
reason), measured request latency, and an estimated `cost_usd` from token usage.

Import is side-effect-free: this module never calls `base.register()`. Registration happens at
wiring time (runner/CLI), so importing `PerplexityAdapter` doesn't implicitly mutate the shared
adapter registry.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from gw_geo.common.models import ProbeResult

_API_URL = "https://api.perplexity.ai/chat/completions"

# USD per 1K tokens, keyed by model name: (prompt_rate, completion_rate). Approximate published
# Perplexity API pricing, kept as a local constant so cost estimation never requires a network
# call. Update here if pricing changes; unlisted models fall back to `_DEFAULT_RATE_PER_1K`.
_RATE_PER_1K_TOKENS_USD: dict[str, tuple[float, float]] = {
    "sonar": (0.001, 0.001),
    "sonar-pro": (0.003, 0.015),
}
_DEFAULT_RATE_PER_1K: tuple[float, float] = (0.001, 0.001)


def _estimate_cost_usd(model: str, usage: dict[str, Any]) -> float:
    """Estimate request cost in USD from a Perplexity `usage` block and the rate table above."""
    prompt_rate, completion_rate = _RATE_PER_1K_TOKENS_USD.get(model, _DEFAULT_RATE_PER_1K)
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    return float(
        (prompt_tokens / 1000) * prompt_rate + (completion_tokens / 1000) * completion_rate
    )


class PerplexityAdapter:
    """`EngineAdapter` for Perplexity's Sonar chat-completions API."""

    name = "perplexity"
    supports_citations = True

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        model: str = "sonar",
    ) -> None:
        self._api_key = api_key
        self._client = client if client is not None else httpx.AsyncClient(timeout=120.0)
        self._model = model

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Probe Perplexity Sonar with `prompt` and map the response onto `ProbeResult`.

        `geo`/`persona` are accepted for `EngineAdapter` conformance; Sonar's chat-completions
        API has no geo/persona targeting parameter today, so they aren't sent upstream (the M1
        Playwright adapters that drive the consumer surface are where locale/persona matter).
        """
        started = time.perf_counter()
        response = await self._client.post(
            _API_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        answer_text: str = payload["choices"][0]["message"]["content"]
        cited_urls: list[str] = list(payload.get("citations", []))
        cost_usd = _estimate_cost_usd(self._model, payload.get("usage", {}))

        return ProbeResult(
            engine=self.name,
            answer_text=answer_text,
            cited_urls=cited_urls,
            raw=payload,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
