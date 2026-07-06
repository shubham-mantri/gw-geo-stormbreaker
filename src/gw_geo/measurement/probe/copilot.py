"""Microsoft Copilot (Bing) engine adapter (TRD §5.2, docs/m1-design.md §2).

Calls the Bing/Copilot grounded-chat endpoint over `httpx` and maps the response onto the
shared `ProbeResult` contract: the grounded answer message, the attributed source URLs Copilot
returns natively, measured request latency, and a flat per-request `cost_usd`.

Endpoint + auth: Microsoft does not publish a standalone "Copilot chat" REST contract separate
from the Bing Search API family, so this adapter follows that family's current, documented
conventions exactly -- same host (`api.bing.microsoft.com`) and the same
`Ocp-Apim-Subscription-Key` subscription-key header every Azure Cognitive Services / Bing Search
API uses (Bing does **not** use OAuth `Authorization: Bearer` tokens). Both are module constants
below (`_API_URL`, `_AUTH_HEADER`) so they are a single, auditable place to re-verify against
Microsoft's docs if the contract changes. `geo` maps to the Bing `mkt` market parameter (a
`<lang>-<COUNTRY>` code, not a bare ISO country code); unmapped geos fall back to `en-US` so
`probe()` never fails solely because of an unmapped market.

Cost model: unlike Perplexity/OpenAI, the Bing Search API family is not documented as
token-billed -- Azure Cognitive Services prices it per call/transaction. `cost_usd` is therefore
a flat per-request rate keyed by model (`_FLAT_RATE_PER_REQUEST_USD`), not a token-usage
calculation.

Import is side-effect-free: this module never calls `base.register()`. Registration happens at
wiring time (`build_runtime`, T18) when `copilot_api_key` is set.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from gw_geo.common.models import ProbeResult

# Bing Search API v7 family host (current for Bing Web/News/Chat-style endpoints). The path below
# is this project's name for the grounded-chat operation described in the TRD; the host + auth
# convention are what's actually verifiable against Microsoft's current docs.
_API_URL = "https://api.bing.microsoft.com/v7.0/copilot/chat/completions"

# Azure Cognitive Services / Bing Search APIs authenticate with a subscription key in this header,
# not an OAuth bearer token -- current, documented convention across the whole Bing API family.
_AUTH_HEADER = "Ocp-Apim-Subscription-Key"

# Bing market codes (`mkt`) are `<lang>-<COUNTRY>`. Map the small set of geos seeded elsewhere in
# this codebase (discover.py / PRD examples); anything unmapped falls back to `_DEFAULT_MKT`.
_GEO_TO_MKT: dict[str, str] = {
    "us": "en-US",
    "gb": "en-GB",
    "in": "en-IN",
    "au": "en-AU",
    "ca": "en-CA",
}
_DEFAULT_MKT = "en-US"

# Flat per-request USD cost, keyed by model (Bing/Copilot bills per-call, not per-token; see
# module docstring). A model absent from the table falls back to `_DEFAULT_FLAT_RATE_USD`.
_FLAT_RATE_PER_REQUEST_USD: dict[str, float] = {
    "copilot": 0.015,
}
_DEFAULT_FLAT_RATE_USD = 0.015


def _estimate_cost_usd(model: str) -> float:
    """Flat per-request USD cost for `model` (see module docstring: Bing is per-call billed)."""
    return _FLAT_RATE_PER_REQUEST_USD.get(model, _DEFAULT_FLAT_RATE_USD)


def _extract_answer(payload: dict[str, Any]) -> tuple[str, list[str]]:
    """Pull the grounded answer text and attributed source URLs from a Copilot payload."""
    answer_text: str = payload.get("answer", {}).get("text", "")
    cited_urls: list[str] = [
        source["url"] for source in payload.get("sourceAttributions", []) if source.get("url")
    ]
    return answer_text, cited_urls


class CopilotAdapter:
    """`EngineAdapter` for Microsoft Copilot (Bing) grounded chat."""

    name = "copilot"
    supports_citations = True

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient | None = None,
        model: str = "copilot",
    ) -> None:
        self._api_key = api_key
        self._client = client if client is not None else httpx.AsyncClient(timeout=120.0)
        self._model = model

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        """Probe Copilot/Bing with `prompt`, mapping `geo` onto the Bing `mkt` market code.

        `persona` is accepted for `EngineAdapter` conformance; the Bing/Copilot chat-completions
        API has no persona-targeting parameter, so it isn't sent upstream -- same documented
        limitation as M0's adapters (persona targeting is a capture-fleet/Playwright concern).
        """
        started = time.perf_counter()
        response = await self._client.post(
            _API_URL,
            headers={_AUTH_HEADER: self._api_key},
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "mkt": _GEO_TO_MKT.get(geo, _DEFAULT_MKT),
            },
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        answer_text, cited_urls = _extract_answer(payload)
        cost_usd = _estimate_cost_usd(self._model)

        return ProbeResult(
            engine=self.name,
            answer_text=answer_text,
            cited_urls=cited_urls,
            raw=payload,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
        )
