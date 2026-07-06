"""Tests for `seeding.brief_llm.PortkeyBriefLLM` -- the real `BriefLLM` backed by the content
engine's injected `LLMClient` (`content.gateway.build_llm_client`).

Hermetic (TRD S12): a `FakeLLMClient` stands in for the `LLMClient` protocol, so no live gateway /
Anthropic call is ever made. The two guarantees under test: (1) `draft_brief` asks the LLM for the
`{talking_points, format_notes}` shape and returns it unchanged, and (2) grounding is still enforced
downstream by `build_brief` (a fabricated talking point not backed by a provided fact is dropped) --
`PortkeyBriefLLM` must not weaken that anti-fabrication guarantee.
"""

from __future__ import annotations

from typing import Any

from gw_geo.common.models import SourceType
from gw_geo.seeding.brief_llm import PortkeyBriefLLM
from gw_geo.seeding.briefs import build_brief
from gw_geo.seeding.channels import ChannelCatalog
from gw_geo.seeding.discovery import SeedingTarget


class FakeLLMClient:
    """Records the last `complete()` call and returns a fixed brief-shaped payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        return self._payload


def _target(channel: str = "reddit", st: SourceType = SourceType.REDDIT) -> SeedingTarget:
    return SeedingTarget(
        channel=channel, source_type=st, domain="reddit.com", engine="perplexity",
        gap_score=0.6, priority=0.9, rationale="competitors cited, you are not",
    )


def test_draft_brief_returns_talking_points_and_asks_for_the_right_shape() -> None:
    fake = FakeLLMClient(
        {"talking_points": ["Acme integrates with X", "Fabricated 99% stat"],
         "format_notes": "Reply as a genuine community member."}
    )
    result = PortkeyBriefLLM(fake).draft_brief(
        target=_target(), facts=["Acme integrates with X"], disclosure="Disclosure: affiliated.",
    )
    assert result["talking_points"] == ["Acme integrates with X", "Fabricated 99% stat"]
    assert result["format_notes"] == "Reply as a genuine community member."

    # It asked the LLM for a talking_points/format_notes schema, and passed the facts + disclosure.
    call = fake.calls[0]
    assert set(call["schema"]["properties"]) >= {"talking_points", "format_notes"}
    assert "Acme integrates with X" in call["prompt"]
    assert "Disclosure: affiliated." in call["prompt"]
    assert call["system"]  # non-empty white-hat system framing


def test_grounding_still_enforced_through_build_brief() -> None:
    # The fabricated stat is not one of the provided facts, so build_brief's _grounded_points
    # drops it -- PortkeyBriefLLM does not (and must not) bypass that filter.
    fake = FakeLLMClient(
        {"talking_points": ["Acme integrates with X", "Fabricated 99% stat"],
         "format_notes": "notes"}
    )
    cat = ChannelCatalog.default()
    brief = build_brief(
        PortkeyBriefLLM(fake), target=_target(), facts=["Acme integrates with X"],
        channel=cat.get("reddit"),
    )
    assert brief.talking_points == ["Acme integrates with X"]
    assert "Fabricated 99% stat" not in brief.grounded_facts
    assert brief.disclosure_text != ""  # reddit requires disclosure


def test_missing_optional_keys_are_tolerated() -> None:
    # A model that omits target_url (and returns nothing groundable) still yields a valid dict.
    fake = FakeLLMClient({"talking_points": [], "format_notes": ""})
    result = PortkeyBriefLLM(fake).draft_brief(
        target=_target("pr_wire", SourceType.NEWS_PR), facts=[], disclosure="",
    )
    assert result["talking_points"] == []
    assert result["format_notes"] == ""
    assert result.get("target_url") is None
