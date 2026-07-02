"""Tests for per-channel seeding briefs (m4-design.md S2.3, docs/tasks/M4-T06-briefs.md).

`docs/tasks/M4-T06-briefs.md` step 1 mandates the two tests below verbatim: a brief built for a
disclosure-required channel (reddit) carries a non-empty disclosure and drops any LLM talking
point not literally backed by a provided brand-KB fact -- the anti-fabrication guarantee -- and a
brief built for a channel that does not require disclosure (pr_wire) carries an empty
`disclosure_text`. `FakeLLM` is a hermetic double for the injected `BriefLLM` protocol -- no live
LLM call.
"""

from gw_geo.common.models import SourceType
from gw_geo.seeding.briefs import build_brief
from gw_geo.seeding.channels import ChannelCatalog
from gw_geo.seeding.discovery import SeedingTarget


class FakeLLM:
    def draft_brief(self, *, target, facts, disclosure):
        return {"talking_points": ["Acme integrates with X", "Made-up 99% stat"],
                "format_notes": f"Write for {target.channel}"}


def _target(channel="reddit", st=SourceType.REDDIT):
    return SeedingTarget(channel=channel, source_type=st, domain="reddit.com",
                         engine="perplexity", gap_score=0.6, priority=0.6, rationale="gap")


def test_brief_is_grounded_and_discloses():
    cat = ChannelCatalog.default()
    brief = build_brief(FakeLLM(), target=_target(), facts=["Acme integrates with X"],
                        channel=cat.get("reddit"))
    assert brief.disclosure_text != ""                     # reddit requires disclosure
    assert "Acme integrates with X" in brief.grounded_facts
    assert "Made-up 99% stat" not in brief.grounded_facts  # not backed by a provided fact
    assert brief.compliance_checklist                      # non-empty guidance


def test_pr_wire_needs_no_disclosure():
    cat = ChannelCatalog.default()
    brief = build_brief(FakeLLM(), target=_target("pr_wire", SourceType.NEWS_PR),
                        facts=["Acme raised a Series B"], channel=cat.get("pr_wire"))
    assert brief.disclosure_text == ""
