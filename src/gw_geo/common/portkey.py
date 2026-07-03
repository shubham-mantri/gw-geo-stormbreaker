"""Raw HTTP client for the Portkey AI gateway (`https://api.portkey.ai/v1`).

Portkey is a self-hosted-config LLM gateway: the content engine sends OpenAI-shaped requests here
(`POST /chat/completions`, `POST /embeddings`) and Portkey routes them to the real provider
(Anthropic, OpenAI, ...) according to the **dashboard Config** referenced by `X-Portkey-Config`
(default `pc-portke-0dd3de`). Provider credentials / virtual keys therefore live in that Config,
never in this codebase; the only secret held here is the Portkey API key itself.

Models are addressed by their **native provider slug** in the request `"model"` field (e.g.
`claude-haiku-4-5-20251001`, `claude-sonnet-4-5`, `text-embedding-3-large`). Structured output is
requested via the OpenAI-style `response_format` (`{"type": "json_schema", ...}`), which Portkey
remaps to the provider's native format server-side, so callers never special-case the provider.

Synchronous `httpx` (matching the established real-client pattern in
`gw_geo.content.generate.AnthropicLLMClient` / `gw_geo.measurement.parse.ClaudeExtractor`). The
hermetic test suite exercises this against a mocked transport (`respx`); no live call is ever made.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class PortkeyClient:
    """Minimal, synchronous client for the Portkey gateway's OpenAI-compatible endpoints.

    All routing/provider selection is delegated to the dashboard Config (`config=`); this client
    only owns the transport, the Portkey headers, and JSON (de)serialization.
    """

    def __init__(
        self,
        *,
        api_key: str,
        config: str,
        base_url: str = "https://api.portkey.ai/v1",
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                "PortkeyClient requires a Portkey API key (set GEO_PORTKEY_API_KEY or pass "
                "api_key=). Provider virtual keys live in the Portkey dashboard Config, but the "
                "gateway key itself must be supplied here."
            )
        self._api_key = api_key
        self._config = config
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self, metadata: dict[str, Any] | None = None) -> dict[str, str]:
        """Build the Portkey request headers, adding `X-Portkey-Metadata` only when supplied."""
        headers = {
            "Content-Type": "application/json",
            "X-Portkey-API-Key": self._api_key,
            "X-Portkey-Config": self._config,
            "x-portkey-strict-open-ai-compliance": "false",
        }
        if metadata is not None:
            headers["X-Portkey-Metadata"] = json.dumps(metadata)
        return headers

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        max_tokens: int = 4096,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST `/chat/completions` and return the parsed JSON response verbatim.

        `response_format` is the OpenAI-style structured-output spec (json_schema); Portkey remaps
        it to the provider's native format. Omitted from the body entirely when `None`.
        """
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if response_format is not None:
            body["response_format"] = response_format
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers=self._headers(metadata),
            json=body,
            timeout=self._timeout,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def embedding(self, *, model: str, text: str) -> list[float]:
        """POST `/embeddings` and return the first embedding vector (`data[0].embedding`)."""
        response = httpx.post(
            f"{self._base_url}/embeddings",
            headers=self._headers(),
            json={"model": model, "input": text},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        vector: list[float] = payload["data"][0]["embedding"]
        return vector


__all__ = ["PortkeyClient"]
