"""Conditioned generation: grounded, feature-shaped `ContentDraft`s (PRD §6.4, TRD §9,
`docs/m3-design.md` §3.2).

`build_generation_prompt` renders the user-turn prompt: it carries every KB `Fact`'s text
verbatim (grounding -- generation may state nothing beyond what's passed in) plus the target
engine's `RankingReport` gaps/factors (shaping -- the draft is nudged toward closing the specific
gaps the ranking model has learned matter, e.g. a low `info_density` gap pushes toward more
quantified stats). `generate_draft` calls an **injected** `LLMClient` with that prompt and maps
its `{title, body_markdown, schema_jsonld}` response into a `DRAFT` `ContentDraft` scoped to the
brand/tenant. `grounded_fact_ids` is stamped from `facts` itself -- `[f.id for f in facts]`,
never parsed out of the LLM's own claims -- so a draft can never claim grounding it wasn't given.

`LLMClient` is a `Protocol`, so the hermetic test suite (`tests/content/test_generate.py`)
injects a `StubLLM` and never makes a live call (`docs/trd.md` §12). A real `AnthropicLLMClient`
(Claude Messages API, tool-use/JSON mode) lives at the bottom of this module for production
wiring, mirroring the established pattern in `gw_geo.measurement.parse.ClaudeExtractor`; it is
never exercised by the unit test suite.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

import httpx

from gw_geo.common.models import Brand, ContentDraft, ContentStatus, Fact, RankingReport
from gw_geo.common.portkey import PortkeyClient

# --------------------------------------------------------------------------------------------
# LLMClient protocol + build_generation_prompt() / generate_draft()
# --------------------------------------------------------------------------------------------


class LLMClient(Protocol):
    """Injectable generation backend. Tests never make a live call (see `StubLLM`)."""

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return `{"title": str, "body_markdown": str, "schema_jsonld": dict}`."""
        ...


_SYSTEM_PROMPT = (
    "You are a content generation engine for a B2B brand's on-site GEO (generative-engine "
    "optimization) content. Generation must be strictly grounded: state only facts explicitly "
    "listed in the knowledge-base facts section of the prompt. Never invent, assume, or add a "
    "statistic, certification, price, or claim that is not present there. Format the draft to "
    "be extraction-friendly for AI search engines -- lead with a direct-answer block, use "
    "tables or lists for comparisons and quantified stats where the facts support them, and "
    "include an FAQPage or HowTo JSON-LD block that reflects the body. Respond only via the "
    "requested tool call."
)

# Response contract every `LLMClient.complete()` implementation fulfils. Passed as the default
# `schema` hint so a real backend (e.g. `AnthropicLLMClient`) can use it as a tool/output schema.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body_markdown": {"type": "string"},
        "schema_jsonld": {
            "type": "object",
            "description": "Extraction-friendly JSON-LD (e.g. FAQPage/HowTo) matching the body.",
        },
    },
    "required": ["title", "body_markdown", "schema_jsonld"],
}


def build_generation_prompt(
    *,
    brand: Brand,
    prompt_text: str,
    facts: list[Fact],
    feature_profile: RankingReport | None,
    intent_cluster: str | None,
) -> str:
    """Build the user-turn prompt for `generate_draft`'s LLM call.

    Grounding: every fact's `.text` is carried into the prompt verbatim, alongside an explicit
    instruction that these are the only facts the draft may state -- this is what makes
    generation grounded rather than hallucinated (PRD §6.4 / `docs/m3-design.md` §3.2).

    Shaping: when `feature_profile` is given, its gap factors (features the engine's ranking
    model has learned are below target) and ranked factors are surfaced so the draft is shaped
    toward closing them.
    """
    lines = [
        f"Brand: {brand.name} ({brand.domain}).",
        f"Target search prompt / question to answer: {prompt_text!r}.",
    ]
    if intent_cluster:
        lines.append(f"Buyer intent cluster: {intent_cluster}.")

    lines.append("")
    lines.append(
        "Knowledge-base facts (the ONLY facts you may state -- do not add, assume, or invent "
        "any other claim about the brand):"
    )
    if facts:
        lines.extend(f"- [{fact.category}] {fact.text}" for fact in facts)
    else:
        lines.append(
            "- (none provided -- do not state any specific claim, stat, price, or "
            "certification; write generic, non-factual guidance only.)"
        )

    if feature_profile is not None:
        lines.append("")
        lines.append(
            f"Learned ranking profile for engine {feature_profile.engine!r} -- shape the draft "
            "to close these gaps and reinforce these factors:"
        )
        lines.extend(
            f"- Gap: {gap.factor} is currently {gap.current_value}, target "
            f"{gap.target_value} for {gap.engine}."
            for gap in feature_profile.gaps
        )
        lines.extend(
            f"- Factor: {factor.name} ({factor.direction}, weight={factor.weight}) -- "
            f"{factor.explanation}"
            for factor in feature_profile.factors
        )

    lines.append("")
    lines.append(
        "Write a direct-answer-first draft formatted for AI-search extraction "
        "(definition-first opening, tables/lists for comparisons, quantified stats where the "
        "facts support them, and an FAQPage or HowTo JSON-LD block reflecting the body)."
    )
    return "\n".join(lines)


def generate_draft(
    *,
    brand: Brand,
    prompt_text: str,
    facts: list[Fact],
    feature_profile: RankingReport | None,
    llm: LLMClient,
    target_engine: str | None = None,
    intent_cluster: str | None = None,
    id_fn: Callable[[], str] | None = None,
) -> ContentDraft:
    """Generate a grounded, feature-conditioned `ContentDraft` via the injected `llm`.

    Grounding is enforced independently of what the LLM claims: `grounded_fact_ids` is stamped
    from `facts` itself (`[f.id for f in facts]`), not parsed out of the response, so a
    generation step can never claim grounding it wasn't given. `tenant_id`/`brand_id` come from
    `brand`; the returned draft's `status` is always `ContentStatus.DRAFT`.

    `id_fn` defaults to a `uuid4`-based factory; inject a deterministic one for tests.
    """
    make_id = id_fn if id_fn is not None else (lambda: str(uuid4()))
    prompt = build_generation_prompt(
        brand=brand,
        prompt_text=prompt_text,
        facts=facts,
        feature_profile=feature_profile,
        intent_cluster=intent_cluster,
    )
    result = llm.complete(system=_SYSTEM_PROMPT, prompt=prompt, schema=_RESPONSE_SCHEMA)

    return ContentDraft(
        id=make_id(),
        tenant_id=brand.tenant_id,
        brand_id=brand.id,
        target_engine=target_engine,
        intent_cluster=intent_cluster,
        title=result["title"],
        body_markdown=result["body_markdown"],
        schema_jsonld=result["schema_jsonld"],
        grounded_fact_ids=[fact.id for fact in facts],
        status=ContentStatus.DRAFT,
    )


# --------------------------------------------------------------------------------------------
# AnthropicLLMClient -- real Claude-backed LLMClient (no live calls in the unit test suite)
# --------------------------------------------------------------------------------------------


class AnthropicLLMClient:
    """`LLMClient` backed by the Claude Messages API in tool-use (JSON-mode) mode.

    Never called by the unit test suite (tests inject `StubLLM` per `docs/trd.md` §12 -- no
    live LLM calls in CI); this is the real implementation used by the content pipeline. Uses
    `httpx` directly (no `anthropic` SDK dependency) against the documented Messages API,
    matching the established pattern in `gw_geo.measurement.parse.ClaudeExtractor`.
    """

    _API_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"
    _DEFAULT_MODEL = "claude-opus-4-8"
    _TOOL_NAME = "record_generated_content"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.environ.get("GEO_ANTHROPIC_API_KEY", "")
        )
        self._model = model or self._DEFAULT_MODEL
        self._timeout = timeout

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError(
                "AnthropicLLMClient requires an Anthropic API key "
                "(pass api_key= or set GEO_ANTHROPIC_API_KEY)."
            )

        response = httpx.post(
            self._API_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self._API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": 4096,
                "system": system,
                "tools": [self._tool_schema(schema)],
                "tool_choice": {"type": "tool", "name": self._TOOL_NAME},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()

        for block in payload.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == self._TOOL_NAME:
                result: dict[str, Any] = block["input"]
                return result

        raise ValueError("Claude response did not include the expected tool_use content block.")

    def _tool_schema(self, schema: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "name": self._TOOL_NAME,
            "description": "Record the generated, grounded content draft.",
            "input_schema": schema if schema is not None else _RESPONSE_SCHEMA,
        }


# --------------------------------------------------------------------------------------------
# PortkeyLLMClient -- LLMClient routed through the Portkey gateway (config-selected in production)
# --------------------------------------------------------------------------------------------


class PortkeyLLMClient:
    """`LLMClient` backed by the Portkey gateway's OpenAI-shaped `/chat/completions` endpoint.

    Matches `AnthropicLLMClient.complete`'s return contract exactly: it always returns the
    structured `{"title", "body_markdown", "schema_jsonld"}` dict, in both the schema and
    no-schema cases. Like `AnthropicLLMClient`, it forces a single function tool (named
    `record_generated_content`) whose parameters are the effective schema -- `schema` when given,
    else `_RESPONSE_SCHEMA` -- and reads the result back from the tool call. This uses OpenAI-style
    function calling (`tools` + `tool_choice`), which Portkey maps to Anthropic **tool-use** (lenient
    about free-form object params), *not* `response_format`: the latter maps to Anthropic strict
    structured-output, which 400s on `_RESPONSE_SCHEMA` because its `schema_jsonld` is a free-form
    object with no declared properties. The structured result arrives as a JSON *string* in
    `choices[0].message.tool_calls[0].function.arguments`, which is `json.loads`-ed here -- so both
    backends return the identical structured shape and `generate_draft` cannot tell them apart.
    Provider selection (which Claude model actually serves the request) lives in the Portkey Config;
    only the native model slug is sent here. Never exercised against a live gateway (tests mock the
    transport).
    """

    _DEFAULT_MODEL = "claude-haiku-4-5-20251001"
    # Matches AnthropicLLMClient._TOOL_NAME so both backends force the same tool.
    _TOOL_NAME = "record_generated_content"

    def __init__(
        self,
        client: PortkeyClient,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self._client = client
        self._model = model or self._DEFAULT_MODEL
        self._max_tokens = max_tokens

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        effective_schema = schema if schema is not None else _RESPONSE_SCHEMA
        payload = self._client.chat_completion(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": self._TOOL_NAME,
                        "description": "Record the generated, grounded content draft.",
                        "parameters": effective_schema,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": self._TOOL_NAME}},
            max_tokens=self._max_tokens,
        )
        arguments = payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
        result: dict[str, Any] = json.loads(arguments)
        return result


__all__ = [
    "AnthropicLLMClient",
    "LLMClient",
    "PortkeyLLMClient",
    "build_generation_prompt",
    "generate_draft",
]
