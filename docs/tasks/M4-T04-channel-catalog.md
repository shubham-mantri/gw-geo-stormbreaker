# M4-T04 — Channel catalog + compliance-rule seed

**Depends on:** T02 (tables), T03 (ruleset) · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** The static, versioned catalog of supported seeding channels (design §2.2) plus a seeder that
persists the catalog into `seeding_channel` and the `ComplianceEngine.default_ruleset()` into
`compliance_rule`. In-process loading returns a `ChannelCatalog` used by discovery (T05), briefs (T06),
and the workflow (T10).

**Files:**
- Create: `src/gw_geo/seeding/channels.py`
- Test: `tests/seeding/test_channels.py`

## Interface

```python
from pydantic import BaseModel
from gw_geo.common.models import SourceType

class Channel(BaseModel):
    name: str
    source_type: SourceType
    tos_ruleset_ref: str
    requires_disclosure: bool
    allows_ugc: bool
    active: bool = True

class ChannelCatalog:
    def __init__(self, channels: list[Channel]) -> None: ...
    def get(self, name: str) -> Channel: ...            # raises KeyError if unknown
    def active(self) -> list[Channel]: ...
    @staticmethod
    def default() -> "ChannelCatalog": ...              # the 8 channels of design §2.2

def seed_channels(session) -> int: ...                  # upsert catalog → seeding_channel
def seed_compliance_rules(session) -> int: ...          # upsert default_ruleset → compliance_rule
def load_catalog(session) -> ChannelCatalog: ...        # read active rows back into a ChannelCatalog
```

The `default()` catalog has exactly the 8 channels from design §2.2: `reddit`(reddit),
`quora`(forum_qa), `g2`(review_site), `capterra`(review_site), `listicle`(listicle),
`wikipedia`(wikipedia), `pr_wire`(news_pr), `expert_byline`(news_pr) — with `requires_disclosure`
true for all except `pr_wire`.

## Steps
- [ ] **1. Failing test** `tests/seeding/test_channels.py` (SQLite):

```python
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from gw_geo.common.db import Base, SeedingChannel, ComplianceRule
from gw_geo.common.models import SourceType
from gw_geo.seeding.channels import (ChannelCatalog, seed_channels,
                                     seed_compliance_rules, load_catalog)

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def test_default_catalog_shape():
    cat = ChannelCatalog.default()
    assert cat.get("wikipedia").source_type == SourceType.WIKIPEDIA
    assert cat.get("wikipedia").requires_disclosure is True
    assert cat.get("g2").allows_ugc is True
    assert len(cat.active()) == 8

def test_seed_and_load_roundtrip():
    s = _session()
    n_ch = seed_channels(s); n_rules = seed_compliance_rules(s); s.commit()
    assert n_ch == 8 and n_rules > 0
    assert s.scalar(select(SeedingChannel).where(SeedingChannel.name == "reddit")) is not None
    assert s.scalar(select(ComplianceRule).where(ComplianceRule.code == "no_astroturf")) is not None
    cat = load_catalog(s)
    assert cat.get("pr_wire").requires_disclosure is False

def test_seed_is_idempotent():
    s = _session()
    seed_channels(s); seed_channels(s); s.commit()
    assert s.query(SeedingChannel).count() == 8
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `channels.py`: the `Channel`/`ChannelCatalog`, `default()`, and idempotent
  `seed_channels`/`seed_compliance_rules` (upsert by unique `name`/`code`), `load_catalog`.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(seeding): channel catalog + compliance-rule seed`

## Acceptance
- `default()` yields the 8 documented channels with correct source-types/disclosure flags; seeding is
  idempotent and populates both `seeding_channel` and `compliance_rule`; `load_catalog` round-trips.
