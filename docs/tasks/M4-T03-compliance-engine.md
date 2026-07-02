# M4-T03 — White-hat compliance rules engine (HARD GATE, PRD NG1)

**Depends on:** M0 models · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** The keystone of M4. A deterministic, unit-tested engine that evaluates a proposed off-site
placement against **global white-hat invariants** (no astroturf / hidden-text / cloaking /
prompt-injection / missing-disclosure — PRD NG1) **and** per-platform ToS rules, returning a
`ComplianceReport` that **blocks** on any block-severity violation. This is a *gate*, not advice
(design §2.4). It performs **no network I/O** — pure evaluation over the proposal.

**Files:**
- Create: `src/gw_geo/seeding/__init__.py` (if absent), `src/gw_geo/seeding/compliance.py`
- Test: `tests/seeding/test_compliance.py`

## Interface (build exactly this — design §2.4)

```python
from typing import Any, Callable, Literal
from pydantic import BaseModel, Field

class ComplianceRule(BaseModel):
    code: str
    channel: str                       # channel name, or "*" for global
    description: str
    severity: Literal["block", "warn"]
    check: str                         # key into the check registry

class ComplianceViolation(BaseModel):
    rule_code: str; severity: str; message: str

class ComplianceReport(BaseModel):
    channel: str
    passed: bool                       # False if ANY block-severity violation
    violations: list[ComplianceViolation] = Field(default_factory=list)

class PlacementProposal(BaseModel):
    channel: str
    body: str
    disclosure_text: str = ""
    author_is_real: bool = True        # attests a real, disclosed human actor
    is_paid: bool = False
    served_to_bots: str | None = None  # if set, must equal body (cloaking check)

CheckFn = Callable[[PlacementProposal], ComplianceViolation | None]

class ComplianceError(Exception): ...

class ComplianceEngine:
    def __init__(self, rules: list[ComplianceRule],
                 checks: dict[str, CheckFn] | None = None) -> None: ...
    def evaluate(self, proposal: PlacementProposal) -> ComplianceReport: ...
    @staticmethod
    def default_ruleset() -> list[ComplianceRule]: ...   # global (NG1) + per-platform
    @staticmethod
    def default_checks() -> dict[str, CheckFn]: ...
```

**Semantics:** `evaluate` runs every rule whose `channel` is `"*"` or equals `proposal.channel`,
resolving `rule.check` in the merged (default ∪ injected) check registry; collects violations;
`passed = no block-severity violation`. Global block checks (must exist): `no_astroturf`
(fails when `author_is_real is False`), `no_hidden_text` (detects zero-opacity / white-on-white /
`display:none` / excessive keyword stuffing in `body`), `no_cloaking` (fails when `served_to_bots`
is set and `!= body`), `no_prompt_injection` (detects "ignore previous instructions"-style crawler
payloads), `disclosure_required` (fails when the channel requires disclosure and
`disclosure_text` is empty). Per-platform examples: `reddit_self_promo_ratio`,
`wikipedia_no_paid_self_edit` (block when `channel=="wikipedia" and is_paid`), `g2_genuine_review`.

## Steps
- [ ] **1. Failing test** `tests/seeding/test_compliance.py`:

```python
import pytest
from gw_geo.seeding.compliance import (ComplianceEngine, PlacementProposal, ComplianceError)

def _engine():
    return ComplianceEngine(ComplianceEngine.default_ruleset())

def test_clean_disclosed_proposal_passes():
    p = PlacementProposal(channel="reddit", body="We build X. Here's a genuine comparison.",
                          disclosure_text="Disclosure: I work at Acme.", author_is_real=True)
    rep = _engine().evaluate(p)
    assert rep.passed is True and rep.violations == []

def test_astroturf_blocks():
    p = PlacementProposal(channel="reddit", body="Acme is the best!",
                          disclosure_text="", author_is_real=False)  # fake identity
    rep = _engine().evaluate(p)
    assert rep.passed is False
    assert any(v.rule_code == "no_astroturf" and v.severity == "block" for v in rep.violations)

def test_hidden_text_blocks():
    p = PlacementProposal(channel="listicle",
        body='<span style="opacity:0">best crm best crm best crm</span> Acme',
        disclosure_text="Sponsored", author_is_real=True)
    rep = _engine().evaluate(p)
    assert rep.passed is False
    assert any(v.rule_code == "no_hidden_text" for v in rep.violations)

def test_cloaking_blocks():
    p = PlacementProposal(channel="g2", body="Human copy", served_to_bots="Different bot copy",
                          disclosure_text="Verified user", author_is_real=True)
    assert _engine().evaluate(p).passed is False

def test_missing_disclosure_blocks_where_required():
    p = PlacementProposal(channel="wikipedia", body="Acme was founded in 2019.",
                          disclosure_text="", author_is_real=True)
    rep = _engine().evaluate(p)
    assert rep.passed is False
    assert any("disclosure" in v.rule_code for v in rep.violations)

def test_wikipedia_paid_self_edit_blocks():
    p = PlacementProposal(channel="wikipedia", body="Acme is the leading vendor.",
                          disclosure_text="COI: Acme employee", is_paid=True, author_is_real=True)
    assert _engine().evaluate(p).passed is False
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `compliance.py`: the models, the default global (NG1) + per-platform ruleset,
  the pure check functions, and `evaluate`. No network, no I/O. Make `default_ruleset` data that can
  later be seeded into `compliance_rule` (T04).
- [ ] **4. Run → pass**; add a `warn`-severity rule test proving a warn does **not** flip `passed`.
- [ ] **5. Commit:** `feat(seeding): white-hat compliance rules engine (NG1 hard gate)`

## Acceptance
- Every global invariant (astroturf, hidden-text, cloaking, prompt-injection, missing-disclosure) and
  the representative per-platform rules **block**; a clean disclosed proposal **passes**; `warn` never
  blocks; engine is pure (no network); mypy-strict-friendly. **This gate is a tested PRD-NG1 contract.**
