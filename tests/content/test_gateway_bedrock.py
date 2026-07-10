"""Hermetic tests verifying the gateway routes to Bedrock when configured."""

from __future__ import annotations

from gw_geo.common.config import Settings
from gw_geo.content.gateway import (
    build_claim_extractor,
    build_llm_client,
    build_voice_scorer,
    resolve_chat_model,
)
from gw_geo.content.guardrails.brand_voice import LLMVoiceScorer
from gw_geo.content.guardrails.claims import LLMClaimExtractor
from gw_geo.content.llm_bedrock import BedrockLLMClient


def _bedrock_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "llm_gateway": "bedrock",
        "bedrock_model_id": "us.anthropic.claude-sonnet-4-20250514",
        "bedrock_region": "us-west-2",
        "aws_region": "us-east-1",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


class TestBuildLlmClientBedrock:
    def test_returns_bedrock_client(self) -> None:
        settings = _bedrock_settings()
        client = build_llm_client(settings)
        assert isinstance(client, BedrockLLMClient)

    def test_uses_configured_model_id(self) -> None:
        settings = _bedrock_settings(bedrock_model_id="us.anthropic.claude-haiku-4-5-20251001")
        client = build_llm_client(settings)
        assert isinstance(client, BedrockLLMClient)
        assert client._model_id == "us.anthropic.claude-haiku-4-5-20251001"

    def test_model_override(self) -> None:
        settings = _bedrock_settings()
        client = build_llm_client(settings, model="custom-model-id")
        assert isinstance(client, BedrockLLMClient)
        assert client._model_id == "custom-model-id"

    def test_uses_bedrock_region(self) -> None:
        settings = _bedrock_settings(bedrock_region="eu-west-1")
        client = build_llm_client(settings)
        assert isinstance(client, BedrockLLMClient)
        assert client._region == "eu-west-1"

    def test_falls_back_to_aws_region(self) -> None:
        settings = _bedrock_settings(bedrock_region="", aws_region="ap-southeast-1")
        client = build_llm_client(settings)
        assert isinstance(client, BedrockLLMClient)
        assert client._region == "ap-southeast-1"


class TestBuildClaimExtractorBedrock:
    def test_returns_llm_claim_extractor_with_bedrock(self) -> None:
        settings = _bedrock_settings()
        extractor = build_claim_extractor(settings)
        assert isinstance(extractor, LLMClaimExtractor)
        assert isinstance(extractor._llm, BedrockLLMClient)

    def test_model_override(self) -> None:
        settings = _bedrock_settings()
        extractor = build_claim_extractor(settings, model="custom-id")
        assert isinstance(extractor, LLMClaimExtractor)
        assert extractor._llm._model_id == "custom-id"  # type: ignore[union-attr]


class TestBuildVoiceScorerBedrock:
    def test_returns_voice_scorer_with_bedrock(self) -> None:
        settings = _bedrock_settings()
        scorer = build_voice_scorer(settings)
        assert isinstance(scorer, LLMVoiceScorer)
        assert isinstance(scorer._llm, BedrockLLMClient)

    def test_model_override(self) -> None:
        settings = _bedrock_settings()
        scorer = build_voice_scorer(settings, model="custom-id")
        assert isinstance(scorer, LLMVoiceScorer)
        assert scorer._llm._model_id == "custom-id"  # type: ignore[union-attr]


class TestResolveChatModelBedrock:
    def test_fallback_returns_bedrock_model_id(self) -> None:
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get.return_value = None
        settings = _bedrock_settings(bedrock_model_id="us.anthropic.claude-opus-4-20250514")
        model = resolve_chat_model(session, gateway="bedrock", settings=settings)
        assert model == "us.anthropic.claude-opus-4-20250514"
