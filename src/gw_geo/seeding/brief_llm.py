"""`BriefLLM` implementation backed by the content engine's injected `LLMClient` (m4 seeding).

`seeding.briefs.build_brief` drafts a channel-shaped seeding brief through the injected `BriefLLM`
protocol (`draft_brief(*, target, facts, disclosure) -> {"talking_points", "format_notes", ...}`).
`PortkeyBriefLLM` is the production implementation of that protocol, layered over the same
`content.generate.LLMClient` the content engine uses -- `content.gateway.build_llm_client(settings)`
returns a Portkey-routed client when the gateway is keyed, else a direct Anthropic client, so
seeding briefs ride the exact routing content generation already does.

**Anti-fabrication is NOT this class's job -- and it must not become it.** `PortkeyBriefLLM` returns
the model's talking points *verbatim*; `build_brief`'s `_grounded_points` then keeps only the ones
literally equal to a caller-supplied brand-KB fact and drops the rest. That fail-closed filter is
the guarantee, so this class deliberately does not pre-filter, "fix up", or vouch for the model's
output -- it only asks (via the prompt + a tool schema) for talking points quoted verbatim from the
provided facts, maximizing how many survive grounding without ever weakening the drop-the-rest rule.

No network call is made by the unit test suite: `LLMClient` is injected (a fake in tests, a real
gateway client in production), mirroring `content.generate.generate_draft` (TRD S12).
"""

from __future__ import annotations

from typing import Any

from gw_geo.content.generate import LLMClient
from gw_geo.seeding.discovery import SeedingTarget

# System framing: white-hat, grounded, human-executed. The draft is preparatory material for a
# human placer -- this step writes nothing live and posts nothing (PRD NG1).
_SYSTEM_PROMPT = (
    "You draft white-hat off-site seeding briefs for a HUMAN placer to review and act on -- you "
    "never post, submit, or publish anything yourself. Talking points must be grounded strictly in "
    "the brand knowledge-base facts provided: quote the relevant facts VERBATIM and never invent, "
    "embellish, or add a statistic, price, certification, or claim not present in them. Recommend "
    "only genuine, disclosed, ToS-compliant participation for the target channel. Respond only via "
    "the requested tool call."
)

# Tool/output schema requested from the LLM. `build_brief` reads `talking_points` (grounded-filtered
# against the facts), `format_notes`, and an optional `target_url` back off this shape.
_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "talking_points": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Each item an exact, verbatim quote of one provided brand-KB fact.",
        },
        "format_notes": {
            "type": "string",
            "description": "How to shape a genuine, compliant contribution for this channel.",
        },
        "target_url": {
            "type": ["string", "null"],
            "description": "A specific existing thread/page to contribute to, if one is evident.",
        },
    },
    "required": ["talking_points", "format_notes"],
}


class PortkeyBriefLLM:
    """`BriefLLM` (see `seeding.briefs`) backed by an injected `content.generate.LLMClient`.

    In production the client is built by `content.gateway.build_llm_client(settings)` (Portkey-routed
    when keyed, else direct Anthropic); tests inject a fake `LLMClient`. Grounding stays enforced by
    `build_brief` downstream (see the module docstring).
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def draft_brief(
        self, *, target: SeedingTarget, facts: list[str], disclosure: str
    ) -> dict[str, Any]:
        """Return `{"talking_points": [...], "format_notes": ..., "target_url": ...}` for `target`.

        Builds a grounded, channel-aware prompt, requests the `_BRIEF_SCHEMA` shape from the
        injected `LLMClient`, and normalizes the response so `build_brief` always finds the keys it
        reads. Talking points are returned as-is (verbatim) -- grounding is applied by `build_brief`.
        """
        prompt = self._build_prompt(target, facts, disclosure)
        result = self._llm.complete(system=_SYSTEM_PROMPT, prompt=prompt, schema=_BRIEF_SCHEMA)
        return {
            "talking_points": [str(point) for point in result.get("talking_points", [])],
            "format_notes": str(result.get("format_notes", "")),
            "target_url": result.get("target_url"),
        }

    @staticmethod
    def _build_prompt(target: SeedingTarget, facts: list[str], disclosure: str) -> str:
        lines = [
            "Draft a white-hat off-site seeding brief for a human placer.",
            f"Channel: {target.channel}. Source type: {target.source_type.value}. "
            f"Target domain: {target.domain}. Cited by engine: {target.engine}.",
            f"Why this target: {target.rationale}",
            "",
            "Brand knowledge-base facts -- the ONLY claims you may use. Select the relevant ones "
            "and return each VERBATIM (character-for-character) as a talking point; do not "
            "paraphrase, combine, or add any claim not listed here:",
        ]
        if facts:
            lines.extend(f"- {fact}" for fact in facts)
        else:
            lines.append("- (none provided -- return an empty talking_points list.)")
        if disclosure:
            lines += [
                "",
                "This channel requires a disclosure; the human placer will include this snippet "
                f"verbatim: {disclosure}",
            ]
        lines += [
            "",
            "Return talking_points (each a verbatim fact) and format_notes describing how to make a "
            f"genuine, disclosed, ToS-compliant contribution on {target.channel}.",
        ]
        return "\n".join(lines)


__all__ = ["PortkeyBriefLLM"]
