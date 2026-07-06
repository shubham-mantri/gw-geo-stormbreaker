"""OpenAI (ChatGPT) engine adapter (TRD §5.2) via the Responses API + `web_search` tool.

Second M0 engine adapter. Uses the Responses API (`POST /v1/responses`) with
`tools=[{"type": "web_search"}]` so answers reflect live retrieval + citations, per TRD §3
("monitor consumer-facing behavior, not static model"). Import side-effect-free: this module
never calls `measurement.probe.base.register()` itself -- that happens at wiring time
(runner/CLI), same convention as the other adapters.
"""

from typing import Any

import httpx

from gw_geo.common.models import ProbeResult

_RESPONSES_URL = "https://api.openai.com/v1/responses"

# USD per 1M tokens, as (input_rate, output_rate). Small, module-local rate table -- extend as
# new models are wired in. A model absent from the table falls back to the gpt-4.1 rate.
_RATE_PER_1M_TOKENS_USD: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
}
_DEFAULT_RATE = _RATE_PER_1M_TOKENS_USD["gpt-4.1"]


def _compute_cost_usd(model: str, usage: dict[str, Any]) -> float:
    """Token-based cost estimate from the Responses API `usage` block."""
    input_tokens = int(usage.get("input_tokens", 0))
    output_tokens = int(usage.get("output_tokens", 0))
    rate_in, rate_out = _RATE_PER_1M_TOKENS_USD.get(model, _DEFAULT_RATE)
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


def _extract_answer(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Concatenate `output_text` text across assistant `message` items; collect cited URLs.

    Citation URLs come from `url_citation` annotations on each `output_text` content block
    (other `output` items, e.g. `web_search_call`, carry no answer text and are skipped). The
    same URL may be annotated more than once -- e.g. cited at several points in the answer --
    so URLs are de-duplicated while preserving first-seen order.
    """
    text_parts: list[str] = []
    cited_urls: list[str] = []
    seen_urls: set[str] = set()

    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") != "output_text":
                continue
            text_parts.append(content.get("text", ""))
            for annotation in content.get("annotations", []):
                if annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    cited_urls.append(url)

    return "".join(text_parts), cited_urls


class OpenAIAdapter:
    """`EngineAdapter` for OpenAI's Responses API with the `web_search` tool enabled."""

    name = "openai"
    supports_citations = True

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        model: str = "gpt-4.1",
    ) -> None:
        self._api_key = api_key
        self._client = client if client is not None else httpx.AsyncClient(timeout=120.0)
        self._model = model

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Probe the OpenAI Responses API with the `web_search` tool enabled.

        `geo`/`persona` are accepted for `EngineAdapter` parity with other adapters; M0's
        request does not yet vary by geo or persona (tracked as a later refinement).
        """
        response = await self._client.post(
            _RESPONSES_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "input": prompt,
                "tools": [{"type": "web_search"}],
            },
        )
        response.raise_for_status()
        latency_ms = int(response.elapsed.total_seconds() * 1000)

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
