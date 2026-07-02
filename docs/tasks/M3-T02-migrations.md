# M3-T02 — DB migrations (feature_model, content_asset, opportunity, guardrail, bandit)

**Depends on:** M0 db · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** SQLAlchemy 2.0 ORM tables + an Alembic migration for the M3 data model (m3-design §6):
`feature_model`, `content_asset`, `content_guardrail_report`, `opportunity`, `bandit_arm`,
`bandit_reward`. Every table is tenant-scoped (TRD §7) with an indexed `tenant_id` FK.

**Files:**
- Edit: `src/gw_geo/common/db.py` (add ORM tables)
- Create: `migrations/versions/0003_m3.py` (Alembic; `0002_m3` if M1 unmerged)
- Test: `tests/common/test_db_m3.py`

## Interface

```python
# db.py — new ORM tables (columns per m3-design §6)
class FeatureModel(Base):
    __tablename__ = "feature_model"
    id, tenant_id(FK,index), brand_id(FK,index), engine: str
    model_type: str
    feature_names: JSON; importances: JSON; metrics: JSON
    trained_at: DateTime

class ContentAsset(Base):
    __tablename__ = "content_asset"
    id, tenant_id(FK,index), brand_id(FK,index)
    type: str                 # "onsite" | "offsite"
    target_engine: str | None; prompt_id: str | None
    title: str; body_s3_key: str | None
    features: JSON; schema_jsonld: JSON
    status: str               # draft|pending_review|approved|published|rejected
    published_url: str | None; connector: str | None
    published_at: DateTime | None; created_at: DateTime

class ContentGuardrailReport(Base):
    __tablename__ = "content_guardrail_report"
    id, tenant_id(FK,index), content_asset_id(FK,index)
    originality_ok: bool; originality_score: float
    claims_ok: bool; unverified_claims: JSON
    brand_voice_ok: bool; brand_voice_score: float
    passed: bool; ts: DateTime

class Opportunity(Base):
    __tablename__ = "opportunity"
    id, tenant_id(FK,index), brand_id(FK,index)
    title: str; rationale: str; engine: str | None
    est_impact: float; source_gap: str
    status: str; created_at: DateTime   # open|acted|dismissed

class BanditArm(Base):
    __tablename__ = "bandit_arm"
    id, tenant_id(FK,index), brand_id(FK,index)
    content_variant: str; channel: str
    alpha: float; beta: float; pulls: int; updated_at: DateTime

class BanditReward(Base):
    __tablename__ = "bandit_reward"
    id, tenant_id(FK,index), arm_id(FK,index)
    reward: float; source_snapshot_id: str | None; ts: DateTime
```

## Steps
- [ ] **1. Failing test** `tests/common/test_db_m3.py` (SQLite in-memory):

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from gw_geo.common.db import (Base, ContentAsset, Opportunity, FeatureModel,
                              ContentGuardrailReport, BanditArm)

def _session():
    eng = create_engine("sqlite://"); Base.metadata.create_all(eng); return Session(eng)

def test_content_asset_roundtrip():
    s = _session()
    s.add(ContentAsset(id="c1", tenant_id="t1", brand_id="b1", type="onsite",
                       title="Best CRM", status="draft"))
    s.commit()
    got = s.get(ContentAsset, "c1")
    assert got is not None and got.tenant_id == "t1" and got.status == "draft"

def test_opportunity_and_feature_model_and_guardrail():
    s = _session()
    s.add(Opportunity(id="o1", tenant_id="t1", brand_id="b1", title="absent on Gemini",
                     rationale="0% mention", engine="gemini", est_impact=0.8, source_gap="absence",
                     status="open"))
    s.add(FeatureModel(id="m1", tenant_id="t1", brand_id="b1", engine="perplexity",
                       model_type="gbt", feature_names=["has_schema"], importances=[0.4], metrics={}))
    s.add(ContentGuardrailReport(id="g1", tenant_id="t1", content_asset_id="c1",
                                 originality_ok=True, originality_score=0.1, claims_ok=True,
                                 unverified_claims=[], brand_voice_ok=True, brand_voice_score=0.9,
                                 passed=True))
    s.commit()
    assert s.get(Opportunity, "o1").source_gap == "absence"
    assert s.get(FeatureModel, "m1").importances == [0.4]
    assert s.get(ContentGuardrailReport, "g1").passed is True
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the ORM tables (mirror M0 `db.py` style: `Mapped[...]` + `mapped_column`,
  `JSON`/`Float`/`Boolean`/`Integer`/`String`/`DateTime`, `tenant_id` indexed FK). Generate the
  Alembic `0003_m3` revision from `Base.metadata` (upgrade creates all six tables, downgrade drops).
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean; `alembic upgrade head` works on a scratch DB.
- [ ] **5. Commit:** `feat(common): M3 schema — content/ranking/opportunity/bandit tables + migration`

## Acceptance
- All six M3 tables created via ORM + Alembic, tenant-scoped with indexed `tenant_id`; SQLite tests
  green; migration up/down clean.
