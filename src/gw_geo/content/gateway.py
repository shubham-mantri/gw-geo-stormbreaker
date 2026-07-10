"""Config-driven factories that route the content engine's LLM + embedding calls.

The content-chat path (generation, seeding briefs, competitor suggestion, claim extraction,
brand-voice scoring) is selected by `Settings.llm_gateway`:

* `"local_claude"` (default) -- run every chat call through the local `claude -p` CLI on the user's
  Claude Max subscription (`LocalClaudeCliClient`), at $0 API cost and needing no key.
* `"portkey"` -- route through the Portkey gateway when keyed (`portkey_api_key`); provider routing
  / virtual keys live in the dashboard Config, never here. Model slugs are Portkey native slugs.
* `"direct"` -- hit the providers directly (Anthropic for chat).

`build_portkey_client` returns a shared `PortkeyClient` only when the gateway is `"portkey"` *and*
keyed, else `None` -- so `llm_gateway="portkey"` without a key degrades gracefully to the direct
path rather than failing, mirroring `gw_geo.common.wiring`'s "skip what isn't configured" posture
(TRD §7). `DEFAULT_CHAT_MODEL` is a deliberately cheap default (Haiku) for the high-volume Portkey
chat + guardrail calls; embeddings default to `Settings.embedding_model`.

Embeddings are **never** served by local Claude (the CLI can't embed): `build_embedder` is
independent of the `local_claude` flag and always returns the Portkey embedder (when keyed) or the
direct OpenAI client.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session as SASession

from gw_geo.common.config import Settings
from gw_geo.common.db import LlmModelConfig
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
from gw_geo.content.llm_bedrock import BedrockLLMClient
from gw_geo.content.llm_local import LocalClaudeCliClient

# Cheap default for the high-volume content + guardrail chat calls; overridable per call site.
DEFAULT_CHAT_MODEL = "claude-haiku-4-5-20251001"


def resolve_chat_model(session: SASession, *, gateway: str, settings: Settings) -> str:
    """The operator-selected content-chat model for `gateway`, or today's constant fallback.

    Reads `llm_model_config.chat_model` for the active `gateway` (the env-driven `GEO_LLM_GATEWAY`,
    passed by the caller -- the gateway itself is *not* DB-stored, only the model is). When no row
    exists (a fresh DB never migrated/seeded, or a gateway an operator never configured) it falls
    back to exactly the constants the factories use with `model=None`, so a migrated and an
    un-migrated DB behave identically: `settings.claude_cli_model` for `local_claude`, else
    `DEFAULT_CHAT_MODEL`. `session` is a plain (unscoped) `Session` -- the config is global, not
    tenant-scoped.
    """
    row = session.get(LlmModelConfig, gateway)
    if row is not None:
        return row.chat_model
    if gateway == "local_claude":
        return settings.claude_cli_model
    if gateway == "bedrock":
        return settings.bedrock_model_id
    return DEFAULT_CHAT_MODEL


def build_portkey_client(settings: Settings) -> PortkeyClient | None:
    """The shared `PortkeyClient`, or `None` when the gateway is off or unconfigured."""
    if settings.llm_gateway != "portkey" or not settings.portkey_api_key:
        return None
    return PortkeyClient(
        api_key=settings.portkey_api_key,
        config=settings.portkey_config,
        base_url=settings.portkey_base_url,
    )


def build_local_claude_client(
    settings: Settings, *, model: str | None = None, allow_web_search: bool = False
) -> LocalClaudeCliClient:
    """The local `claude -p` `LLMClient` (Claude Max subscription, $0), built from `claude_cli_*`.

    `model` overrides the CLI `--model`; when `None` it keeps today's default
    (`settings.claude_cli_model`), so existing callers are unchanged. Call sites with a DB session
    pass the operator-selected model via `resolve_chat_model`.

    `allow_web_search` (default `False`) opts the CLI into a **real web search** (`--allowedTools
    WebSearch`, $0 on the subscription) for grounded research -- used only by the onboarding
    competitor-suggest research/draft stage. Off by default, so every other call site is unchanged.
    """
    return LocalClaudeCliClient(
        bin=settings.claude_cli_bin,
        model=model if model is not None else settings.claude_cli_model,
        config_dir=settings.claude_cli_config_dir,
        timeout=settings.claude_cli_timeout_s,
        allow_web_search=allow_web_search,
    )


def build_llm_client(
    settings: Settings, *, model: str | None = None, allow_web_search: bool = False
) -> LLMClient:
    """The generation `LLMClient`: local Claude when `local_claude`, else Portkey (keyed) or direct.

    `model` (the DB-resolved chat model, `resolve_chat_model`) is threaded through whichever gateway
    is selected; when `None` each path keeps its prior default (`settings.claude_cli_model` local,
    `DEFAULT_CHAT_MODEL` Portkey, the client's own `_DEFAULT_MODEL` direct), so existing
    callers/tests are unchanged.

    `allow_web_search` (default `False`) is honored **only on the `local_claude` path** -- it turns
    on the CLI's real WebSearch tool for grounded onboarding research. Portkey/direct have no local
    CLI web search, so the flag is a no-op there (they return their usual plain client); the suggest
    pipeline still runs its hardened prompt + critique pass, just without web grounding (graceful
    degrade). Every non-suggest caller omits the flag and is unaffected.
    """
    if settings.llm_gateway == "local_claude":
        return build_local_claude_client(settings, model=model, allow_web_search=allow_web_search)
    if settings.llm_gateway == "bedrock":
        return BedrockLLMClient(
            model_id=model if model is not None else settings.bedrock_model_id,
            region=settings.bedrock_region or settings.aws_region,
        )
    client = build_portkey_client(settings)
    if client is not None:
        return PortkeyLLMClient(client, model=model if model is not None else DEFAULT_CHAT_MODEL)
    return AnthropicLLMClient(api_key=settings.anthropic_api_key, model=model)


def build_embedder(settings: Settings) -> EmbeddingClient:
    """The KB `EmbeddingClient` -- **independent of the chat gateway** (Claude can't embed).

    Use Portkey whenever a Portkey key is configured and the gateway isn't forced ``direct``, else
    direct OpenAI. This is what lets ``llm_gateway=local_claude`` work end-to-end: chat runs on the
    local Claude subscription ($0) while embeddings still route through Portkey's funded provider
    (direct OpenAI may be quota-blocked). Only ``llm_gateway=direct`` forces direct OpenAI here.
    """
    if settings.llm_gateway != "direct" and settings.portkey_api_key:
        return PortkeyEmbeddingClient(
            PortkeyClient(
                api_key=settings.portkey_api_key,
                config=settings.portkey_config,
                base_url=settings.portkey_base_url,
            ),
            model=settings.embedding_model,
        )
    return OpenAIEmbeddingClient(api_key=settings.openai_api_key, model=settings.embedding_model)


def build_claim_extractor(settings: Settings, *, model: str | None = None) -> ClaimExtractor:
    """The claim-verification `ClaimExtractor`: local Claude when `local_claude`, Bedrock when
    `bedrock`, else Portkey/direct.

    `model` threads the DB-resolved chat model through the selected gateway; `None` preserves each
    path's prior default (see `build_llm_client`)."""
    if settings.llm_gateway == "local_claude":
        return LLMClaimExtractor(llm=build_local_claude_client(settings, model=model))
    if settings.llm_gateway == "bedrock":
        return LLMClaimExtractor(
            llm=BedrockLLMClient(
                model_id=model if model is not None else settings.bedrock_model_id,
                region=settings.bedrock_region or settings.aws_region,
            )
        )
    client = build_portkey_client(settings)
    if client is not None:
        return LLMClaimExtractor(
            portkey=client, model=model if model is not None else DEFAULT_CHAT_MODEL
        )
    return LLMClaimExtractor(api_key=settings.anthropic_api_key, model=model)


def build_voice_scorer(settings: Settings, *, model: str | None = None) -> VoiceScorer:
    """The brand-voice `VoiceScorer`: local Claude when `local_claude`, Bedrock when `bedrock`,
    else Portkey/direct.

    `model` threads the DB-resolved chat model through the selected gateway; `None` preserves each
    path's prior default (see `build_llm_client`)."""
    if settings.llm_gateway == "local_claude":
        return LLMVoiceScorer(llm=build_local_claude_client(settings, model=model))
    if settings.llm_gateway == "bedrock":
        return LLMVoiceScorer(
            llm=BedrockLLMClient(
                model_id=model if model is not None else settings.bedrock_model_id,
                region=settings.bedrock_region or settings.aws_region,
            )
        )
    client = build_portkey_client(settings)
    if client is not None:
        return LLMVoiceScorer(
            portkey=client, model=model if model is not None else DEFAULT_CHAT_MODEL
        )
    return LLMVoiceScorer(api_key=settings.anthropic_api_key, model=model)


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
    "build_local_claude_client",
    "build_portkey_client",
    "build_voice_scorer",
    "resolve_chat_model",
]
