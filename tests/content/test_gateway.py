"""Hermetic tests for the Portkey-backed content clients + the gateway factories.

Every HTTP call is served by a mocked transport (`respx`) -- no live call to Portkey or OpenAI is
ever made. These exercise: the Portkey-backed `LLMClient` / `EmbeddingClient` and the direct
`OpenAIEmbeddingClient`; the guardrail LLM clients routed through Portkey; and the config-driven
factories that pick Portkey vs. direct.
"""

import json

import httpx
import pytest
import respx

from gw_geo.common.config import Settings
from gw_geo.common.models import Brand, Fact
from gw_geo.common.portkey import PortkeyClient
from gw_geo.content.gateway import (
    build_claim_extractor,
    build_embedder,
    build_llm_client,
    build_portkey_client,
    build_voice_scorer,
)
from gw_geo.content.generate import (
    _RESPONSE_SCHEMA,
    AnthropicLLMClient,
    PortkeyLLMClient,
    generate_draft,
)
from gw_geo.content.guardrails.brand_voice import LLMVoiceScorer
from gw_geo.content.guardrails.claims import LLMClaimExtractor
from gw_geo.content.kb import OpenAIEmbeddingClient, PortkeyEmbeddingClient
from gw_geo.content.llm_local import LocalClaudeCliClient

BASE = "https://api.portkey.ai/v1"
BRAND = Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com")
FACTS = [
    Fact(id="f1", brand_id="b1", text="Acme is SOC2 Type II certified", category="certification"),
    Fact(id="f2", brand_id="b1", text="Plans start at $29/mo", category="pricing"),
]


def _portkey() -> PortkeyClient:
    return PortkeyClient(api_key="pk-test", config="pc-portke-0dd3de")


def _chat_route(
    payload: dict[str, object], *, name: str = "record_generated_content"
) -> respx.Route:
    # Portkey maps the OpenAI-style forced function tool to Anthropic tool-use, so a structured
    # response comes back as a tool call whose `function.arguments` is a JSON *string*.
    return respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"function": {"name": name, "arguments": json.dumps(payload)}}
                            ]
                        }
                    }
                ]
            },
        )
    )


# --------------------------------------------------------------------------------------------
# PortkeyLLMClient -- LLMClient backed by the gateway
# --------------------------------------------------------------------------------------------


@respx.mock
def test_portkey_llm_complete_with_schema_parses_structured_json() -> None:
    payload = {"title": "T", "body_markdown": "B", "schema_jsonld": {"@type": "FAQPage"}}
    route = _chat_route(payload)
    schema = {"type": "object", "properties": {"title": {"type": "string"}}}
    llm = PortkeyLLMClient(_portkey(), model="claude-haiku-4-5-20251001")

    result = llm.complete(system="you are a bot", prompt="write it", schema=schema)

    assert result == payload
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["messages"] == [
        {"role": "system", "content": "you are a bot"},
        {"role": "user", "content": "write it"},
    ]
    # Structured output via a forced OpenAI-style function tool (Portkey -> Anthropic tool-use),
    # NOT response_format (which Anthropic strict structured-output 400s on a free-form object).
    assert "response_format" not in body
    tool = body["tools"][0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "record_generated_content"
    assert tool["function"]["parameters"] == schema  # the schema rides through as tool params
    assert isinstance(tool["function"]["description"], str) and tool["function"]["description"]
    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": "record_generated_content"},
    }


@respx.mock
def test_portkey_llm_no_schema_still_returns_structured_dict() -> None:
    # Matches AnthropicLLMClient.complete's contract: even with schema=None the client falls back
    # to the module response schema and returns the structured dict (never free text).
    payload = {"title": "T", "body_markdown": "B", "schema_jsonld": {}}
    route = _chat_route(payload)
    result = PortkeyLLMClient(_portkey()).complete(system="s", prompt="p")
    assert result == payload
    body = json.loads(route.calls.last.request.content)
    assert "response_format" not in body
    # Falls back to the module _RESPONSE_SCHEMA -- whose free-form `schema_jsonld` object (no
    # declared properties) is exactly what made response_format 400 -- carried as tool params.
    assert body["tools"][0]["function"]["parameters"] == _RESPONSE_SCHEMA
    assert body["tool_choice"]["function"]["name"] == "record_generated_content"


@respx.mock
def test_generate_draft_works_end_to_end_through_portkey() -> None:
    payload = {
        "title": "Best CRM",
        "body_markdown": "## Answer\nAcme is SOC2 Type II certified.",
        "schema_jsonld": {"@type": "FAQPage"},
    }
    _chat_route(payload)
    draft = generate_draft(
        brand=BRAND,
        prompt_text="best crm",
        facts=FACTS,
        feature_profile=None,
        llm=PortkeyLLMClient(_portkey()),
        id_fn=lambda: "c1",
    )
    assert draft.title == "Best CRM"
    assert draft.schema_jsonld == {"@type": "FAQPage"}
    assert draft.grounded_fact_ids == ["f1", "f2"]  # grounding stamped from facts, not the LLM


# --------------------------------------------------------------------------------------------
# Embedding clients -- Portkey-backed and direct-OpenAI
# --------------------------------------------------------------------------------------------


@respx.mock
def test_portkey_embedding_client_embeds_via_gateway() -> None:
    route = respx.post(f"{BASE}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.5, 0.25]}]})
    )
    vec = PortkeyEmbeddingClient(_portkey(), model="text-embedding-3-large").embed("hello")
    assert vec == [0.5, 0.25]
    assert json.loads(route.calls.last.request.content) == {
        "model": "text-embedding-3-large",
        "input": "hello",
    }
    assert route.calls.last.request.headers["x-portkey-api-key"] == "pk-test"


@respx.mock
def test_openai_embedding_client_hits_openai_directly() -> None:
    route = respx.post("https://api.openai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [1.0, 2.0]}]})
    )
    vec = OpenAIEmbeddingClient(api_key="sk-test", model="text-embedding-3-large").embed("hello")
    assert vec == [1.0, 2.0]
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer sk-test"
    assert json.loads(req.content) == {"model": "text-embedding-3-large", "input": "hello"}


def test_openai_embedding_client_requires_key() -> None:
    with pytest.raises(RuntimeError):
        OpenAIEmbeddingClient(api_key="").embed("x")


# A sanity check that the direct LLM client is still importable/constructible (unchanged path).
def test_direct_anthropic_llm_client_still_constructible() -> None:
    assert isinstance(AnthropicLLMClient(api_key="a"), AnthropicLLMClient)


# --------------------------------------------------------------------------------------------
# Guardrail LLM clients routed through Portkey (direct-Anthropic path preserved)
# --------------------------------------------------------------------------------------------


@respx.mock
def test_llm_claim_extractor_routes_through_portkey() -> None:
    route = _chat_route({"claims": ["Acme is SOC2 Type II certified"]}, name="record_claims")
    extractor = LLMClaimExtractor(portkey=_portkey(), model="claude-haiku-4-5-20251001")
    claims = extractor.extract_claims("Acme is SOC2 Type II certified. It is great.")
    assert claims == ["Acme is SOC2 Type II certified"]  # parsed from tool_calls[0] arguments
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "claude-haiku-4-5-20251001"
    assert body["messages"][0]["role"] == "user"  # claim extractor uses no system turn
    # Same tool-use path as the LLM client: forced function tool, not response_format.
    assert "response_format" not in body
    assert body["tools"][0]["function"]["name"] == "record_claims"
    assert body["tool_choice"] == {"type": "function", "function": {"name": "record_claims"}}


def test_llm_claim_extractor_direct_path_still_requires_key() -> None:
    # No Portkey wired + empty Anthropic key => the direct path still fails closed (unchanged).
    with pytest.raises(RuntimeError):
        LLMClaimExtractor(api_key="").extract_claims("x")


@respx.mock
def test_llm_voice_scorer_routes_through_portkey() -> None:
    route = _chat_route({"score": 0.9, "violations": []}, name="record_voice_score")
    scorer = LLMVoiceScorer(portkey=_portkey())
    result = scorer.score("some draft", {"tone": "friendly", "banned": ["synergy"]})
    assert result == {"score": 0.9, "violations": []}  # parsed from tool_calls[0] arguments
    body = json.loads(route.calls.last.request.content)
    assert "response_format" not in body
    assert body["tools"][0]["function"]["name"] == "record_voice_score"
    assert body["tool_choice"] == {"type": "function", "function": {"name": "record_voice_score"}}


def test_llm_voice_scorer_direct_path_still_requires_key() -> None:
    with pytest.raises(RuntimeError):
        LLMVoiceScorer(api_key="").score("x", {"tone": "t", "banned": []})


# --------------------------------------------------------------------------------------------
# Factories -- select Portkey vs. direct by llm_gateway / key presence
# --------------------------------------------------------------------------------------------


def test_build_portkey_client_none_when_gateway_direct() -> None:
    assert build_portkey_client(Settings(llm_gateway="direct", portkey_api_key="pk")) is None


def test_build_portkey_client_none_when_key_missing() -> None:
    assert build_portkey_client(Settings(llm_gateway="portkey", portkey_api_key="")) is None


def test_build_portkey_client_built_when_gateway_portkey_and_keyed() -> None:
    client = build_portkey_client(Settings(llm_gateway="portkey", portkey_api_key="pk"))
    assert isinstance(client, PortkeyClient)


def test_build_llm_client_selects_by_gateway() -> None:
    portkey = build_llm_client(Settings(llm_gateway="portkey", portkey_api_key="pk"))
    direct = build_llm_client(Settings(llm_gateway="direct", anthropic_api_key="a"))
    assert isinstance(portkey, PortkeyLLMClient)
    assert isinstance(direct, AnthropicLLMClient)


def test_build_embedder_selects_by_gateway() -> None:
    portkey = build_embedder(Settings(llm_gateway="portkey", portkey_api_key="pk"))
    direct = build_embedder(Settings(llm_gateway="direct", openai_api_key="o"))
    assert isinstance(portkey, PortkeyEmbeddingClient)
    assert isinstance(direct, OpenAIEmbeddingClient)


def test_factories_fall_back_to_direct_when_portkey_key_missing() -> None:
    # gateway="portkey" but unconfigured (no key) => graceful fallback to the direct impls.
    s = Settings(llm_gateway="portkey", portkey_api_key="")
    assert isinstance(build_llm_client(s), AnthropicLLMClient)
    assert isinstance(build_embedder(s), OpenAIEmbeddingClient)


@respx.mock
def test_build_claim_extractor_portkey_routes_through_gateway() -> None:
    route = _chat_route({"claims": ["c1"]})
    s = Settings(llm_gateway="portkey", portkey_api_key="pk")
    claims = build_claim_extractor(s).extract_claims("some draft")
    assert claims == ["c1"]
    assert route.called


def test_build_claim_extractor_direct_uses_anthropic_path() -> None:
    # direct + no Anthropic key => the direct path raises, proving it is NOT wired to Portkey.
    s = Settings(llm_gateway="direct", anthropic_api_key="")
    with pytest.raises(RuntimeError):
        build_claim_extractor(s).extract_claims("some draft")


@respx.mock
def test_build_voice_scorer_portkey_routes_through_gateway() -> None:
    route = _chat_route({"score": 0.8, "violations": []})
    s = Settings(llm_gateway="portkey", portkey_api_key="pk")
    result = build_voice_scorer(s).score("draft", {"tone": "t", "banned": []})
    assert result == {"score": 0.8, "violations": []}
    assert route.called


# --------------------------------------------------------------------------------------------
# Local Claude CLI gateway (llm_gateway="local_claude") -- subscription-billed, $0 API
# --------------------------------------------------------------------------------------------


class _StubLLM:
    """A minimal `LLMClient` recording its calls and returning a canned structured dict."""

    def __init__(self, result: dict[str, object]) -> None:
        self._result = result
        self.calls: list[tuple[str, str, object]] = []

    def complete(
        self, *, system: str, prompt: str, schema: object = None
    ) -> dict[str, object]:
        self.calls.append((system, prompt, schema))
        return self._result


def test_build_llm_client_local_claude_returns_local_cli_client() -> None:
    # local_claude wins over both portkey and direct (no key needed -- the Claude Max subscription).
    client = build_llm_client(Settings(llm_gateway="local_claude", portkey_api_key="pk"))
    assert isinstance(client, LocalClaudeCliClient)


def test_build_llm_client_selects_all_three_gateways() -> None:
    assert isinstance(build_llm_client(Settings(llm_gateway="local_claude")), LocalClaudeCliClient)
    assert isinstance(
        build_llm_client(Settings(llm_gateway="portkey", portkey_api_key="pk")), PortkeyLLMClient
    )
    assert isinstance(
        build_llm_client(Settings(llm_gateway="direct", anthropic_api_key="a")), AnthropicLLMClient
    )


def test_build_claim_extractor_local_claude_routes_to_local_client() -> None:
    extractor = build_claim_extractor(Settings(llm_gateway="local_claude"))
    assert isinstance(extractor, LLMClaimExtractor)
    assert isinstance(extractor._llm, LocalClaudeCliClient)  # wired to the $0 subscription backend


def test_build_voice_scorer_local_claude_routes_to_local_client() -> None:
    scorer = build_voice_scorer(Settings(llm_gateway="local_claude"))
    assert isinstance(scorer, LLMVoiceScorer)
    assert isinstance(scorer._llm, LocalClaudeCliClient)


def test_llm_claim_extractor_local_seam_reads_claims_off_dict() -> None:
    # The local-Claude seam goes through LLMClient.complete(..., schema=...) and reads `claims`.
    stub = _StubLLM({"claims": ["Acme is SOC2 Type II certified"]})
    claims = LLMClaimExtractor(llm=stub).extract_claims("Acme is SOC2 Type II certified. Great.")
    assert claims == ["Acme is SOC2 Type II certified"]
    system, prompt, schema = stub.calls[0]
    assert isinstance(schema, dict) and "claims" in schema["properties"]  # forced structured schema
    assert "Acme is SOC2 Type II certified. Great." in prompt


def test_llm_voice_scorer_local_seam_returns_dict() -> None:
    stub = _StubLLM({"score": 0.9, "violations": []})
    result = LLMVoiceScorer(llm=stub).score("draft", {"tone": "friendly", "banned": ["synergy"]})
    assert result == {"score": 0.9, "violations": []}
    _system, prompt, schema = stub.calls[0]
    assert isinstance(schema, dict) and set(schema["properties"]) == {"score", "violations"}
    assert "synergy" in prompt  # banned term carried into the prompt
