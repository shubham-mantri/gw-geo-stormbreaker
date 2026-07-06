"""Guardrail: brand-voice conformance (`docs/prd.md` §6.4, `docs/trd.md` §9).

Scores a draft's conformance to the brand voice profile via an **injected** `VoiceScorer`;
content is on-voice iff `score >= min_score` (default 0.7, inclusive). `check_brand_voice` is
pure orchestration and is exercised in the hermetic unit tests via a stub `VoiceScorer` -- no
live LLM calls there (`docs/trd.md` §12). The real implementation, `LLMVoiceScorer`, calls the
Claude Messages API in tool-use (JSON-mode) via `httpx` directly (no `anthropic` SDK
dependency), mirroring `gw_geo.measurement.parse.ClaudeExtractor`; it is never exercised by the
unit test suite.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

import httpx

from gw_geo.common.portkey import PortkeyClient
from gw_geo.content.generate import LLMClient

# System turn used only on the local-Claude (`LLMClient`) path -- the direct-Anthropic / Portkey
# transports carry the instruction in the user turn. Parse behavior is identical: every path reads
# the same `{"score", "violations"}` object off `_input_schema()`.
_VOICE_SYSTEM = (
    "You score how well a content draft conforms to a brand's voice profile, from 0.0 (not at all "
    "on-voice) to 1.0 (perfect conformance), and list any specific violations. Respond only via "
    "the requested tool call / structured output."
)


class VoiceScorer(Protocol):
    def score(self, text: str, voice_profile: dict[str, Any]) -> dict[str, Any]:
        """Return `{"score": float (0..1), "violations": list[str]}`."""
        ...


def check_brand_voice(
    draft_text: str,
    voice_profile: dict[str, Any],
    *,
    scorer: VoiceScorer,
    min_score: float = 0.7,
) -> tuple[bool, float, list[str]]:
    """Score `draft_text` against `voice_profile` via the injected `scorer`.

    Returns `(ok, score, violations)`, where `ok = score >= min_score` (boundary inclusive).
    """
    result = scorer.score(draft_text, voice_profile)
    score = float(result["score"])
    violations = list(result["violations"])
    return score >= min_score, score, violations


class LLMVoiceScorer:
    """`VoiceScorer` backed by an LLM in structured-output (JSON) mode.

    Three transports, same prompt + same parsed `{"score", "violations"}` result: by default it
    calls the Claude Messages API directly via `httpx` (tool-use / `anthropic`-SDK-free); when an
    optional `PortkeyClient` is injected it instead sends the identical prompt through the Portkey
    gateway's OpenAI-shaped `/chat/completions`, forcing the same schema via an OpenAI-style function
    tool (`tools` + `tool_choice`) and reading the score out of
    `choices[0].message.tool_calls[0].function.arguments`; when an optional local-Claude `LLMClient`
    is injected (gateway `local_claude`) it goes through `LLMClient.complete(..., schema=...)` and
    returns the dict verbatim, for a $0 subscription-billed run. Function calling (mapped by Portkey
    to Anthropic tool-use) is used rather than `response_format` for the same reason as the LLM
    client: it maps to the provider's lenient tool-use, avoiding the strict structured-output failure
    class. Never called by the unit test suite (tests inject a stub `VoiceScorer` per `docs/trd.md`
    §12); the Portkey + local paths are exercised in `tests/content/test_gateway.py` against mocked
    transports.
    """

    _API_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"
    _DEFAULT_MODEL = "claude-opus-4-8"
    _TOOL_NAME = "record_voice_score"

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

    def score(self, text: str, voice_profile: dict[str, Any]) -> dict[str, Any]:
        if self._llm is not None:
            local_result: dict[str, Any] = self._llm.complete(
                system=_VOICE_SYSTEM,
                prompt=self._prompt(text, voice_profile),
                schema=self._input_schema(),
            )
            return local_result

        if self._portkey is not None:
            payload = self._portkey.chat_completion(
                model=self._model,
                messages=[{"role": "user", "content": self._prompt(text, voice_profile)}],
                tools=[self._function_tool()],
                tool_choice={"type": "function", "function": {"name": self._TOOL_NAME}},
                max_tokens=1024,
            )
            arguments = payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
            routed: dict[str, Any] = json.loads(arguments)
            return routed

        if not self._api_key:
            raise RuntimeError(
                "LLMVoiceScorer requires an Anthropic API key "
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
                "messages": [{"role": "user", "content": self._prompt(text, voice_profile)}],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()

        for block in payload.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == self._TOOL_NAME:
                result: dict[str, Any] = block["input"]
                return result

        raise ValueError("Claude response did not include the expected tool_use score block.")

    def _tool_schema(self) -> dict[str, Any]:
        return {
            "name": self._TOOL_NAME,
            "description": "Record the brand-voice conformance score for a draft.",
            "input_schema": self._input_schema(),
        }

    def _function_tool(self) -> dict[str, Any]:
        """OpenAI-style function tool for the Portkey path (maps to Anthropic tool-use)."""
        return {
            "type": "function",
            "function": {
                "name": self._TOOL_NAME,
                "description": "Record the brand-voice conformance score for a draft.",
                "parameters": self._input_schema(),
            },
        }

    @staticmethod
    def _input_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "score": {
                    "type": "number",
                    "description": "Conformance to the brand voice profile, from 0.0 "
                    "(not at all on-voice) to 1.0 (perfect conformance).",
                },
                "violations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific voice violations found (e.g. a banned term, "
                    "a tone mismatch, off-brand phrasing). Empty if fully on-voice.",
                },
            },
            "required": ["score", "violations"],
        }

    @staticmethod
    def _prompt(text: str, voice_profile: dict[str, Any]) -> str:
        tone = voice_profile.get("tone", "")
        banned = ", ".join(voice_profile.get("banned", [])) or "none listed"
        return (
            f"Brand voice profile -- tone: {tone}. Banned terms/phrases: {banned}.\n\n"
            f"Draft text to evaluate:\n---\n{text}\n---\n\n"
            "Using the record_voice_score tool, score how well the draft conforms to the "
            "brand voice profile from 0.0 (not at all) to 1.0 (perfect conformance), and list "
            "any specific violations (e.g. use of a banned term, a tone mismatch, off-brand "
            "phrasing). Return an empty violations list if the draft is fully on-voice."
        )
