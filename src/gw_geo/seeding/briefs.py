"""Per-channel seeding briefs: grounded, disclosure-required drafts for a human placer
(m4-design.md S2.3).

Given a `SeedingTarget` (T05 -- the ranked off-site opportunity) and a set of brand-KB facts,
`build_brief` produces a **channel-shaped brief**: talking points, the required disclosure
snippet, format guidance, and a compliance checklist -- everything a human needs to write and
review a placement themselves. The LLM that drafts the talking points is an **injected `BriefLLM`
protocol** (Claude/GPT in production, a fake in tests, per `docs/trd.md` S12) so this module is
hermetic and makes no live call.

**Anti-fabrication guarantee.** `BriefLLM.draft_brief` is untrusted free text: nothing stops a
model from inventing a stat that sounds plausible. `build_brief` never takes the LLM's talking
points on faith -- it keeps only the ones that are *literally* one of the caller-supplied `facts`
(the brand knowledge base) and drops the rest, so `grounded_facts` (and, by construction, the
brief's `talking_points`) can only ever contain a talking point the caller already vouched for.
This mirrors the same fail-closed philosophy as `content.guardrails.claims.verify_claims` (drop
anything unverified) and `content.generate.generate_draft` (stamp grounding from the input, never
parse it out of the model's own claims) -- gw-geo never lets a generation step assert its own
groundedness.

**Disclosure.** `disclosure_text` is derived solely from `channel.requires_disclosure` (T04): a
non-empty affiliation/compensation disclosure whenever the channel demands one, and `""` only for
a channel that does not (e.g. `pr_wire` -- wire distribution of factual, non-misleading content
has no affiliation to disclose). This is independent of whatever the LLM returns, so a brief can
never omit a disclosure a channel requires. The generated snippet also names the FTC-style
affiliation/compensation vocabulary (`compliance.py`'s `g2_genuine_review` check looks for
"incentiv"/"compensat"/"sponsor"-type language before treating a disclosure as adequate for a
paid placement), so a placement built from this brief's disclosure is compliance-ready as-is.

**Compliance checklist.** Rather than duplicate ToS knowledge, `_compliance_checklist` reads the
channel's key constraints straight from `ComplianceEngine.default_ruleset()` (T03) -- the global
white-hat invariants (PRD NG1) that apply everywhere, plus any rule specific to this channel. That
keeps the checklist a human sees here and the rules `ComplianceEngine.evaluate` actually gates on
(T10's workflow) from ever drifting apart.

Briefs are drafts only: nothing here posts, schedules, or submits anything. A human reviews the
brief, writes the placement, and the seeding workflow (T10) still runs the full compliance gate
before anything may reach `placed`.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from gw_geo.seeding.channels import Channel
from gw_geo.seeding.compliance import ComplianceEngine
from gw_geo.seeding.discovery import SeedingTarget

# Generic (brand-name-free -- build_brief is not given a Brand) affiliation/compensation
# disclosure. Deliberately uses the vocabulary compliance.py's `g2_genuine_review` check looks
# for ("compensat"/"sponsor") so a placement built straight from this brief already reads as an
# adequate disclosure for a paid/incentivized placement, not just an unpaid one.
_DISCLOSURE_TEMPLATE = (
    "Disclosure: I'm affiliated with the brand discussed here (employee, sponsored, or otherwise "
    "compensated contribution) -- posted transparently per {channel}'s guidelines and FTC "
    "endorsement-disclosure rules."
)


class SeedingBrief(BaseModel):
    """A channel-shaped brief for a human placer (m4-design.md S2.3). Never auto-published."""

    channel: str
    target_url: str | None = None
    talking_points: list[str]
    grounded_facts: list[str]  # from brand KB -- no fabricated stats
    disclosure_text: str
    format_notes: str
    compliance_checklist: list[str] = Field(default_factory=list)


class BriefLLM(Protocol):
    """Injected brief-drafting backend. Untrusted: its output is grounded-filtered, never taken
    on faith (see module docstring). Real impl = Claude/GPT; tests inject a fake -- no live calls.
    """

    def draft_brief(
        self, *, target: SeedingTarget, facts: list[str], disclosure: str
    ) -> dict[str, Any]:
        """Return at least `{"talking_points": list[str], "format_notes": str}`."""
        ...


def _disclosure_text(channel: Channel) -> str:
    """The required disclosure snippet for `channel`, or `""` iff disclosure is not required."""
    if not channel.requires_disclosure:
        return ""
    return _DISCLOSURE_TEMPLATE.format(channel=channel.name)


def _grounded_points(points: list[str], facts: list[str]) -> list[str]:
    """Keep only entries of `points` that are literally one of `facts`, in `points` order.

    This is the anti-fabrication guarantee: a talking point not backed word-for-word by a
    caller-supplied fact is dropped rather than surfaced to the human placer. Matching against a
    `set` (rather than repeated `list` scans) keeps this cheap for a large fact base; membership
    is by exact string equality, which is also what guarantees the result is a true subset of
    `facts`.
    """
    fact_set = set(facts)
    return [point for point in points if point in fact_set]


def _compliance_checklist(channel: Channel) -> list[str]:
    """The channel's key ToS constraints, read from `ComplianceEngine.default_ruleset()` (T03).

    Sourced from the same ruleset `ComplianceEngine.evaluate` gates on -- the global white-hat
    invariants (`channel="*"`, PRD NG1) always apply, plus any rule specific to `channel.name` --
    so this checklist can never drift from what the compliance engine actually enforces, and is
    always non-empty (every channel is subject to at least the global rules).
    """
    return [
        f"{rule.code} ({rule.severity}): {rule.description}"
        for rule in ComplianceEngine.default_ruleset()
        if rule.channel in ("*", channel.name)
    ]


def build_brief(
    llm: BriefLLM,
    *,
    target: SeedingTarget,
    facts: list[str],
    channel: Channel,
) -> SeedingBrief:
    """Build a grounded, disclosure-required `SeedingBrief` for `channel` (m4-design.md S2.3).

    Disclosure is derived from `channel.requires_disclosure` up front and handed to `llm` as
    context, not left for the model to decide. `llm.draft_brief`'s `talking_points` are then
    grounded-filtered against `facts` (see `_grounded_points`): only points literally backed by a
    provided fact survive, into both `talking_points` and `grounded_facts` -- so nothing in the
    returned brief can state a claim the caller didn't already vouch for. `format_notes` and an
    optional `target_url` are passed through from the LLM's response; `compliance_checklist` is
    sourced independently from `channel` (see `_compliance_checklist`), not from the LLM.

    No network I/O; `llm` is called exactly once.
    """
    disclosure = _disclosure_text(channel)
    result = llm.draft_brief(target=target, facts=facts, disclosure=disclosure)

    grounded = _grounded_points(result["talking_points"], facts)

    return SeedingBrief(
        channel=channel.name,
        target_url=result.get("target_url"),
        talking_points=grounded,
        grounded_facts=grounded,
        disclosure_text=disclosure,
        format_notes=result["format_notes"],
        compliance_checklist=_compliance_checklist(channel),
    )
