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

from collections.abc import Callable

from gw_geo.common.config import Settings
from gw_geo.common.portkey import PortkeyClient
from gw_geo.content.generate import AnthropicLLMClient, LLMClient, PortkeyLLMClient
from gw_geo.content.guardrails.brand_voice import LLMVoiceScorer, VoiceScorer
from gw_geo.content.guardrails.claims import ClaimExtractor, LLMClaimExtractor
from gw_geo.content.kb import (
    EmbeddingClient,
    KnowledgeBase,
    OpenAIEmbeddingClient,
    PortkeyEmbeddingClient,
    build_vector_store,
)

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


def build_kb_factory(settings: Settings) -> Callable[[str], KnowledgeBase]:
    """A per-brand `KnowledgeBase` builder from `settings`.

    Returns a `brand_id -> KnowledgeBase` factory the content service + KB-ingest endpoint call to
    ground/verify/populate a specific brand's corpus. The embedder is shared across brands (it is
    brand-agnostic), while the vector store is rebuilt per brand via `build_vector_store(...,
    brand_id=...)` -- pgvector filters every query on `brand_id`, Pinecone namespaces on it -- so KB
    access can never cross a brand boundary. Construction does no I/O: the embedder + store clients
    connect lazily, on first embed/query, never here (so this is safe to call at request time)."""
    embedder = build_embedder(settings)

    def _factory(brand_id: str) -> KnowledgeBase:
        return KnowledgeBase(
            brand_id=brand_id,
            store=build_vector_store(settings, brand_id=brand_id),
            embedder=embedder,
        )

    return _factory


__all__ = [
    "DEFAULT_CHAT_MODEL",
    "build_claim_extractor",
    "build_embedder",
    "build_kb_factory",
    "build_llm_client",
    "build_portkey_client",
    "build_voice_scorer",
]
