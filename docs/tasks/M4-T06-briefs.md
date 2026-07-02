# M4-T06 — Per-channel briefs (grounded, disclosure-required)

**Depends on:** T03 (compliance), T05 (targets) · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** Given a `SeedingTarget` + brand knowledge-base facts, produce a **channel-shaped brief** for
a human placer: grounded talking points (no fabricated stats), the required disclosure snippet, format
guidance, and a compliance checklist (design §2.3). The LLM is an **injected `BriefLLM` protocol**
(Claude/GPT in prod, fake in tests) — no live calls in CI, briefs are drafts, never auto-published.

**Files:**
- Create: `src/gw_geo/seeding/briefs.py`
- Test: `tests/seeding/test_briefs.py`

## Interface (design §2.3)

```python
from typing import Any, Protocol
from pydantic import BaseModel, Field
from gw_geo.seeding.discovery import SeedingTarget
from gw_geo.seeding.channels import Channel

class SeedingBrief(BaseModel):
    channel: str
    target_url: str | None = None
    talking_points: list[str]
    grounded_facts: list[str]           # from brand KB — no fabricated stats
    disclosure_text: str
    format_notes: str
    compliance_checklist: list[str] = Field(default_factory=list)

class BriefLLM(Protocol):
    def draft_brief(self, *, target: SeedingTarget, facts: list[str],
                    disclosure: str) -> dict[str, Any]: ...

def build_brief(llm: BriefLLM, *, target: SeedingTarget, facts: list[str],
                channel: Channel) -> SeedingBrief: ...
```

`build_brief` derives the disclosure text from `channel.requires_disclosure` (empty allowed only when
disclosure is not required), calls `llm.draft_brief`, and assembles a `SeedingBrief` whose
`grounded_facts ⊆ facts` (drops any point not supported by a provided fact) and whose
`compliance_checklist` names the channel's key ToS constraints.

## Steps
- [ ] **1. Failing test** `tests/seeding/test_briefs.py`:

```python
from gw_geo.common.models import SourceType
from gw_geo.seeding.channels import ChannelCatalog
from gw_geo.seeding.discovery import SeedingTarget
from gw_geo.seeding.briefs import build_brief

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
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `build_brief`: disclosure derivation, `llm.draft_brief` call, grounding filter
  (`grounded_facts ⊆ facts`), checklist from channel constraints. No network.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(seeding): grounded per-channel brief builder`

## Acceptance
- Brief carries a disclosure when the channel requires it (empty only when not required); talking
  points are grounded to provided facts (unsupported ones dropped); checklist non-empty; injected LLM,
  hermetic. Briefs are drafts — nothing is posted.
