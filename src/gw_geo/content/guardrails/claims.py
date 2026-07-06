"""Guardrail: claim verification vs. the brand knowledge base (`docs/prd.md` §6.4, §13).

The **anti-hallucination guard**: extracts atomic factual claims from a draft via an **injected**
`ClaimExtractor` and verifies each against the brand `KnowledgeBase` (T06). A claim is verified iff
`KnowledgeBase.ground_scored` yields at least one supporting `Fact` with similarity
`>= sim_threshold` (default 0.8, matching `Settings.claim_sim_threshold`); `claims_ok` is `True`
only when *every* extracted claim is verified -- fail-closed, so a single fabricated/ungrounded
claim sinks the whole draft. This is the specific check Athena's documented fabrication failure
needed (PRD §13): a stat invented outright, with nothing in the brand's approved facts to support
it, must never reach publish.

`verify_claims` is pure orchestration and is exercised in the hermetic unit tests via a stub
`ClaimExtractor` and an in-memory `KnowledgeBase` -- no live LLM/embedding/vector-store calls there
(`docs/trd.md` §12). The real implementation, `LLMClaimExtractor`, calls the Claude Messages API in
tool-use (JSON-mode) via `httpx` directly (no `anthropic` SDK dependency), mirroring
`gw_geo.content.guardrails.brand_voice.LLMVoiceScorer`; it is never exercised by the unit test
suite.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

import httpx

from gw_geo.common.portkey import PortkeyClient
from gw_geo.content.generate import LLMClient
from gw_geo.content.kb import KnowledgeBase

# System turn used only on the local-Claude (`LLMClient`) path -- the direct-Anthropic / Portkey
# transports carry the instruction in the user turn (no system turn). Parse behavior is identical:
# every path reads the `claims` list off the same `_input_schema()`.
_CLAIMS_SYSTEM = (
    "You extract the atomic, checkable factual claims from a content draft (each a stat, "
    "certification, pricing detail, or similar assertion). Respond only via the requested tool "
    "call / structured output."
)


class ClaimExtractor(Protocol):
    """Injected: pulls the atomic factual claims out of a draft. Real impl = LLM
    (`LLMClaimExtractor`); tests inject a stub -- no live calls in the hermetic suite.
    """

    def extract_claims(self, text: str) -> list[str]:
        """Return every atomic factual claim made in `text` (one stat/assertion per item)."""
        ...


def verify_claims(
    draft_text: str,
    *,
    kb: KnowledgeBase,
    extractor: ClaimExtractor,
    sim_threshold: float = 0.8,
) -> tuple[bool, list[str]]:
    """Extract `draft_text`'s factual claims via `extractor` and verify each against `kb`.

    A claim is verified iff `kb.ground_scored(claim)` returns at least one `Fact` whose similarity
    is `>= sim_threshold` (boundary inclusive, mirroring `check_brand_voice`'s `score >= min_score`).

    Fail-closed: `claims_ok` is `True` only when *every* extracted claim is verified. A draft from
    which `extractor` pulls no claims trivially passes -- there is nothing left unverified -- but a
    single unverified claim, fabricated or merely un-grounded, forces `claims_ok = False`.

    Returns:
        `(claims_ok, unverified_claims)`, where `claims_ok = (unverified_claims == [])` and
        `unverified_claims` lists the offending claims verbatim, in extraction order.
    """
    unverified = [
        claim
        for claim in extractor.extract_claims(draft_text)
        if not _is_grounded(claim, kb=kb, sim_threshold=sim_threshold)
    ]
    return not unverified, unverified


def _is_grounded(claim: str, *, kb: KnowledgeBase, sim_threshold: float) -> bool:
    """Whether `kb` has at least one `Fact` supporting `claim` at/above `sim_threshold`."""
    return any(score >= sim_threshold for _, score in kb.ground_scored(claim))


class LLMClaimExtractor:
    """`ClaimExtractor` backed by an LLM in structured-output (JSON) mode.

    Three transports, same prompt + same parsed result (a list of claim strings): by default it
    calls the Claude Messages API directly via `httpx` (tool-use / `anthropic`-SDK-free); when an
    optional `PortkeyClient` is injected it instead sends the identical prompt through the Portkey
    gateway's OpenAI-shaped `/chat/completions`, forcing the same schema via an OpenAI-style function
    tool (`tools` + `tool_choice`) and reading the claims out of
    `choices[0].message.tool_calls[0].function.arguments`; when an optional local-Claude `LLMClient`
    is injected (gateway `local_claude`) it goes through `LLMClient.complete(..., schema=...)` -- the
    same structured contract as generation -- reading `claims` off the returned dict, for a $0
    subscription-billed run. Function calling (mapped by Portkey to Anthropic tool-use) is used
    rather than `response_format` for the same reason as the LLM client: it maps to the provider's
    lenient tool-use, avoiding the strict structured-output failure class. Never called by the unit
    test suite (tests inject a stub `ClaimExtractor` per `docs/trd.md` §12); the Portkey + local
    paths are exercised in `tests/content/test_gateway.py` against mocked transports.
    """

    _API_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"
    _DEFAULT_MODEL = "claude-opus-4-8"
    _TOOL_NAME = "record_claims"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        timeout: float = 30.0,
        portkey: PortkeyClient | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.environ.get("GEO_ANTHROPIC_API_KEY", "")
        )
        self._model = model or self._DEFAULT_MODEL
        self._timeout = timeout
        self._portkey = portkey
        self._llm = llm

    def extract_claims(self, text: str) -> list[str]:
        if self._llm is not None:
            result = self._llm.complete(
                system=_CLAIMS_SYSTEM, prompt=self._prompt(text), schema=self._input_schema()
            )
            local_claims: list[str] = result["claims"]
            return local_claims

        if self._portkey is not None:
            payload = self._portkey.chat_completion(
                model=self._model,
                messages=[{"role": "user", "content": self._prompt(text)}],
                tools=[self._function_tool()],
                tool_choice={"type": "function", "function": {"name": self._TOOL_NAME}},
                max_tokens=1024,
            )
            arguments = payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
            routed: list[str] = json.loads(arguments)["claims"]
            return routed

        if not self._api_key:
            raise RuntimeError(
                "LLMClaimExtractor requires an Anthropic API key "
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
                "max_tokens": 1024,
                "tools": [self._tool_schema()],
                "tool_choice": {"type": "tool", "name": self._TOOL_NAME},
                "messages": [{"role": "user", "content": self._prompt(text)}],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()

        for block in payload.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == self._TOOL_NAME:
                claims: list[str] = block["input"]["claims"]
                return claims

        raise ValueError("Claude response did not include the expected tool_use claims block.")

    def _tool_schema(self) -> dict[str, Any]:
        return {
            "name": self._TOOL_NAME,
            "description": "Record the atomic factual claims made in a content draft.",
            "input_schema": self._input_schema(),
        }

    def _function_tool(self) -> dict[str, Any]:
        """OpenAI-style function tool for the Portkey path (maps to Anthropic tool-use)."""
        return {
            "type": "function",
            "function": {
                "name": self._TOOL_NAME,
                "description": "Record the atomic factual claims made in a content draft.",
                "parameters": self._input_schema(),
            },
        }

    @staticmethod
    def _input_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "claims": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Every atomic, checkable factual claim in the draft "
                    "(a stat, certification, pricing detail, or similar assertion), each "
                    "given as a standalone string taken verbatim from the text. Empty if "
                    "the draft makes no factual claims.",
                },
            },
            "required": ["claims"],
        }

    @staticmethod
    def _prompt(text: str) -> str:
        return (
            f"Draft text to analyze:\n---\n{text}\n---\n\n"
            "Using the record_claims tool, extract every atomic factual claim made in the "
            "draft -- a stat, certification, pricing detail, or other checkable assertion -- "
            "each as a standalone, verbatim string. Return an empty list if the draft makes no "
            "factual claims."
        )
