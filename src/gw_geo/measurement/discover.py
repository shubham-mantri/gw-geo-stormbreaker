"""Discover: build a brand's prompt-universe from seed topics (TRD §5.1).

v0 scope (per `docs/tasks/M0-T11-discover.md`): seed topics are expanded into buyer-intent
phrasings via an injected `PromptExpander` — an LLM paraphrase step in production, a
deterministic stub in tests (no live calls in the unit suite; see `StubExpander`/`Dup` in
the test module). Intent clustering v0 is just the label the expander assigns per phrase;
embedding-based clustering (Pinecone) and real search-volume estimation are M1/v2 work.

A real `LLMExpander` (Claude-backed) lives here too, but — like `ClaudeExtractor` in
`measurement/parse.py` — is never exercised by the hermetic unit tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol
from uuid import uuid4

import httpx

from gw_geo.common.config import get_settings
from gw_geo.common.models import Brand, Prompt

# --------------------------------------------------------------------------------------------
# PromptExpander protocol + build_prompt_set()
# --------------------------------------------------------------------------------------------


class PromptExpander(Protocol):
    """Injectable prompt-expansion backend.

    `expand` returns up to `size` candidate prompts for `seed_topics`, each a
    `{"text": str, "intent_cluster": str}` dict. `build_prompt_set` treats the result as
    untrusted input and re-applies dedupe/cap itself, so an expander is free to over-return,
    under-return, or ignore `size` entirely (real LLM output will do all three).
    """

    def expand(self, brand: Brand, seed_topics: list[str], size: int) -> list[dict[str, Any]]: ...


def _new_prompt_id() -> str:
    return str(uuid4())


def build_prompt_set(
    brand: Brand,
    seed_topics: list[str],
    size: int,
    expander: PromptExpander,
    id_fn: Callable[[], str] | None = None,
) -> list[Prompt]:
    """Build a brand's prompt universe from `seed_topics` via the injected `expander`.

    Rules (TRD §5.1 / `docs/tasks/M0-T11-discover.md`):
    - dedupe candidates by normalized (stripped, lowercased) text, keeping the first
      occurrence in the expander's returned order;
    - cap the result at `size`, regardless of how many candidates the expander returns;
    - every `Prompt` is scoped to `brand.tenant_id` / `brand.id`, with `intent_cluster`
      carried through verbatim from the expander's dict.

    `id_fn` defaults to a `uuid4`-based factory; inject a deterministic one for tests.
    """
    make_id = id_fn if id_fn is not None else _new_prompt_id
    candidates = expander.expand(brand, seed_topics, size)

    prompts: list[Prompt] = []
    seen_text: set[str] = set()
    for candidate in candidates:
        if len(prompts) >= size:
            break

        text = candidate["text"]
        normalized = text.strip().lower()
        if normalized in seen_text:
            continue
        seen_text.add(normalized)

        prompts.append(
            Prompt(
                id=make_id(),
                tenant_id=brand.tenant_id,
                brand_id=brand.id,
                text=text,
                intent_cluster=candidate.get("intent_cluster"),
                volume_estimate=0.0,  # v0 pluggable proxy (TRD §5.1); real volume is M1/v2.
            )
        )

    return prompts


# --------------------------------------------------------------------------------------------
# LLMExpander — real Claude-backed PromptExpander (no live calls in the unit test suite)
# --------------------------------------------------------------------------------------------


class LLMExpander:
    """`PromptExpander` backed by the Claude Messages API.

    Never called by the unit test suite (tests inject `StubExpander`/`Dup`-style stubs —
    no live LLM calls in CI); this is the real implementation used by the runner. Uses
    `httpx` directly (no `anthropic` SDK dependency), matching
    `measurement.parse.ClaudeExtractor`. Reads its API key from
    `gw_geo.common.config.Settings` by default; nothing here makes a network call at import
    time.
    """

    _API_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"
    _DEFAULT_MODEL = "claude-sonnet-5"
    _TOOL_NAME = "record_prompt_expansion"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key if api_key is not None else get_settings().anthropic_api_key
        self._model = model or self._DEFAULT_MODEL
        self._timeout = timeout

    def expand(self, brand: Brand, seed_topics: list[str], size: int) -> list[dict[str, Any]]:
        if not self._api_key:
            raise RuntimeError(
                "LLMExpander requires an Anthropic API key "
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
                "max_tokens": 2048,
                "tools": [self._tool_schema()],
                "tool_choice": {"type": "tool", "name": self._TOOL_NAME},
                "messages": [
                    {"role": "user", "content": self._prompt(brand, seed_topics, size)}
                ],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()

        for block in payload.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == self._TOOL_NAME:
                prompts: list[dict[str, Any]] = block["input"].get("prompts", [])
                return prompts

        raise ValueError("Claude response did not include the expected tool_use expansion block.")

    def _tool_schema(self) -> dict[str, Any]:
        return {
            "name": self._TOOL_NAME,
            "description": "Record the expanded buyer-intent prompt universe for a brand.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "prompts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "intent_cluster": {"type": "string"},
                            },
                            "required": ["text", "intent_cluster"],
                        },
                    },
                },
                "required": ["prompts"],
            },
        }

    @staticmethod
    def _prompt(brand: Brand, seed_topics: list[str], size: int) -> str:
        topics = ", ".join(seed_topics)
        return (
            f"Brand: {brand.name} ({brand.domain}).\n\n"
            f"Seed topics: {topics}.\n\n"
            f"Using the {LLMExpander._TOOL_NAME} tool, generate up to {size} realistic "
            "buyer-intent prompts that a prospective customer might type into an AI search "
            "engine (e.g. Perplexity, ChatGPT) while researching options in this space. "
            "Paraphrase and expand each seed topic into natural-language questions spanning "
            "the buyer journey (awareness, evaluation, comparison, decision). For each "
            "prompt, assign a short `intent_cluster` label (e.g. 'evaluation', "
            "'comparison', 'pricing', 'how-to'). Do not mention the brand by name in the "
            "generated prompts — these simulate a prospective customer who does not yet "
            "know the brand."
        )


__all__ = ["LLMExpander", "PromptExpander", "build_prompt_set"]
