"""Config-driven factories that route the content engine's LLM + embedding calls.

`build_portkey_client` returns a shared `PortkeyClient` when the gateway is both selected
(`Settings.llm_gateway == "portkey"`) and keyed (`Settings.portkey_api_key` set), otherwise `None`.
Every other factory returns the **Portkey-backed** implementation when that client is available and
the **direct** provider client (Anthropic for chat, OpenAI for embeddings) otherwise -- so selecting
`llm_gateway="portkey"` without a key degrades gracefully to the direct path rather than failing,
mirroring `gw_geo.common.wiring`'s "skip what isn't configured" posture (TRD §7).

Model slugs are Portkey **native slugs**; provider routing / virtual keys are held in the Portkey
dashboard Config, never here. `DEFAULT_CHAT_MODEL` is a deliberately cheap default (Haiku) for the
high-volume content + guardrail calls; embeddings default to `Settings.embedding_model`.
"""

from __future__ import annotations

from gw_geo.common.config import Settings
from gw_geo.common.portkey import PortkeyClient
from gw_geo.content.generate import AnthropicLLMClient, LLMClient, PortkeyLLMClient
from gw_geo.content.guardrails.brand_voice import LLMVoiceScorer, VoiceScorer
from gw_geo.content.guardrails.claims import ClaimExtractor, LLMClaimExtractor
from gw_geo.content.kb import EmbeddingClient, OpenAIEmbeddingClient, PortkeyEmbeddingClient

# Cheap default for the high-volume content + guardrail chat calls; overridable per call site.
DEFAULT_CHAT_MODEL = "claude-haiku-4-5-20251001"


def build_portkey_client(settings: Settings) -> PortkeyClient | None:
    """The shared `PortkeyClient`, or `None` when the gateway is off or unconfigured."""
    if settings.llm_gateway != "portkey" or not settings.portkey_api_key:
        return None
    return PortkeyClient(
        api_key=settings.portkey_api_key,
        config=settings.portkey_config,
        base_url=settings.portkey_base_url,
    )


def build_llm_client(settings: Settings) -> LLMClient:
    """The generation `LLMClient`: Portkey-backed when available, else direct Anthropic."""
    client = build_portkey_client(settings)
    if client is not None:
        return PortkeyLLMClient(client, model=DEFAULT_CHAT_MODEL)
    return AnthropicLLMClient(api_key=settings.anthropic_api_key)


def build_embedder(settings: Settings) -> EmbeddingClient:
    """The KB `EmbeddingClient`: Portkey-backed when available, else direct OpenAI."""
    client = build_portkey_client(settings)
    if client is not None:
        return PortkeyEmbeddingClient(client, model=settings.embedding_model)
    return OpenAIEmbeddingClient(api_key=settings.openai_api_key, model=settings.embedding_model)


def build_claim_extractor(settings: Settings) -> ClaimExtractor:
    """The claim-verification `ClaimExtractor`: Portkey-routed when available, else direct Anthropic."""
    client = build_portkey_client(settings)
    if client is not None:
        return LLMClaimExtractor(portkey=client, model=DEFAULT_CHAT_MODEL)
    return LLMClaimExtractor(api_key=settings.anthropic_api_key)


def build_voice_scorer(settings: Settings) -> VoiceScorer:
    """The brand-voice `VoiceScorer`: Portkey-routed when available, else direct Anthropic."""
    client = build_portkey_client(settings)
    if client is not None:
        return LLMVoiceScorer(portkey=client, model=DEFAULT_CHAT_MODEL)
    return LLMVoiceScorer(api_key=settings.anthropic_api_key)


__all__ = [
    "DEFAULT_CHAT_MODEL",
    "build_claim_extractor",
    "build_embedder",
    "build_llm_client",
    "build_portkey_client",
    "build_voice_scorer",
]
