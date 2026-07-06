"""DeepSeek engine adapter (TRD §5.2; config-toggled off by default per TRD OT3).

Calls DeepSeek's OpenAI-compatible chat-completions API (`POST /chat/completions`) and maps the
response onto the shared `ProbeResult` contract: the assistant's answer text, a measured request
latency, and an estimated `cost_usd` from token usage. DeepSeek chat returns no first-class
citations, so `supports_citations = False` and `cited_urls` is always empty -- the T10 contract
suite permits an empty `cited_urls` list for adapters that declare `supports_citations = False`.

Import is side-effect-free: this module never calls `base.register()`. Registration happens at
wiring time (`build_runtime`, T18) and -- unlike the other API adapters -- is additionally gated
there on `deepseek_enabled` being `True` (TRD OT3: DeepSeek/Doubao gated on APAC client demand),
so importing this module never implicitly enables DeepSeek probing.
"""

from typing import Any

import httpx

from gw_geo.common.models import ProbeResult

_API_URL = "https://api.deepseek.com/chat/completions"

# USD per 1M tokens, keyed by model name: (prompt_rate, completion_rate). Approximate published
# DeepSeek API pricing, kept as a local constant so cost estimation never requires a network
# call. Update here if pricing changes; unlisted models fall back to `_DEFAULT_RATE`.
_RATE_PER_1M_TOKENS_USD: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}
_DEFAULT_RATE = _RATE_PER_1M_TOKENS_USD["deepseek-chat"]


def _estimate_cost_usd(model: str, usage: dict[str, Any]) -> float:
    """Estimate request cost in USD from a DeepSeek `usage` block and the rate table above."""
    prompt_rate, completion_rate = _RATE_PER_1M_TOKENS_USD.get(model, _DEFAULT_RATE)
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    return float((prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 1_000_000)


class DeepSeekAdapter:
    """`EngineAdapter` for DeepSeek's OpenAI-compatible chat-completions API.

    Always implemented and contract-tested; only its *registration* in `build_runtime` is
    toggle-gated on `deepseek_enabled` (TRD OT3, wired in T18).
    """

    name = "deepseek"
    supports_citations = False

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        model: str = "deepseek-chat",
    ) -> None:
        self._api_key = api_key
        self._client = client if client is not None else httpx.AsyncClient(timeout=120.0)
        self._model = model

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Probe DeepSeek chat with `prompt` and map the response onto `ProbeResult`.

        `geo`/`persona` are accepted for `EngineAdapter` conformance; DeepSeek's
        chat-completions API has no geo/persona targeting parameter today, so they aren't sent
        upstream.
        """
        response = await self._client.post(
            _API_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        response.raise_for_status()
        latency_ms = int(response.elapsed.total_seconds() * 1000)
        payload: dict[str, Any] = response.json()

        answer_text: str = payload["choices"][0]["message"]["content"]
        cost_usd = _estimate_cost_usd(self._model, payload.get("usage", {}))

        return ProbeResult(
            engine=self.name,
            answer_text=answer_text,
            cited_urls=[],
            raw=payload,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
