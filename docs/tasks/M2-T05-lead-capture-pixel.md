# M2-T05 — Lead-capture pixel/SDK + ingestion

**Depends on:** T02 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** The first-party lead-capture SDK (`web/pixel/gwgeo.ts`) and its backend ingestion
(`attribution/ingest.py` + `POST /lead-capture/collect`). This is the **origin of direct-referral
data** (TRD §6.1, m2-design §6). Beacon endpoint is public but **write-key-scoped** (key → tenant/brand
server-side; write-only, per brand).

**Files:**
- Create: `src/gw_geo/attribution/ingest.py`, `src/gw_geo/api/routers/leadcapture.py`,
  `web/pixel/gwgeo.ts`
- Test: `tests/attribution/test_ingest.py`, `tests/api/test_leadcapture.py`,
  `web/pixel/gwgeo.test.ts`

## Interface

```python
# ingest.py
class SessionEvent(BaseModel):
    tenant_id: str; brand_id: str; visitor_id: str
    landing_url: str; referrer: str | None = None
    utm: dict[str, str] = Field(default_factory=dict)
    user_agent: str | None = None; ts: datetime

class LeadEvent(BaseModel):
    tenant_id: str; brand_id: str; visitor_id: str
    email: str | None = None; value_usd: float | None = None
    crm_stage: str | None = None; self_reported_source: str | None = None; ts: datetime

def ingest_session(session, ev: SessionEvent) -> str: ...   # -> session.id
def ingest_lead(session, ev: LeadEvent) -> str: ...         # -> lead.id, links latest session_id
def resolve_write_key(session, key: str, *, salt: str) -> tuple[str, str]: ...  # key -> (tenant_id, brand_id)
```

`POST /lead-capture/collect` (public): body `{write_key, type:"session"|"lead", ...fields}` →
`resolve_write_key` → `ingest_*` → `202 {ok:true}`. Never returns tenant data.

## Steps
- [ ] **1. Failing tests.** `tests/attribution/test_ingest.py`:

```python
from datetime import datetime, UTC
from gw_geo.attribution.ingest import ingest_session, ingest_lead, SessionEvent, LeadEvent

def test_session_then_lead_links(seeded_session):     # seeded_session has tenant t1 + brand b1
    sid = ingest_session(seeded_session, SessionEvent(
        tenant_id="t1", brand_id="b1", visitor_id="v1",
        landing_url="https://acme.com/crm", referrer="https://chatgpt.com/",
        ts=datetime.now(UTC)))
    lid = ingest_lead(seeded_session, LeadEvent(
        tenant_id="t1", brand_id="b1", visitor_id="v1", email="a@x.com",
        value_usd=1000.0, ts=datetime.now(UTC)))
    from gw_geo.common.db import Lead
    lead = seeded_session.get(Lead, lid) if hasattr(seeded_session, "get") else None
    assert sid and lid
```

`tests/api/test_leadcapture.py`:

```python
def test_collect_is_public_and_writes(app_client, seeded_brand_writekey):  # -> (write_key)
    r = app_client.post("/lead-capture/collect", json={
        "write_key": seeded_brand_writekey, "type": "session",
        "visitor_id": "v1", "landing_url": "https://acme.com/crm",
        "referrer": "https://perplexity.ai/"})
    assert r.status_code == 202 and r.json()["ok"] is True

def test_collect_rejects_bad_key(app_client):
    r = app_client.post("/lead-capture/collect", json={"write_key":"bad","type":"session",
        "visitor_id":"v","landing_url":"https://a.com"})
    assert r.status_code in (401, 403)
```

`web/pixel/gwgeo.test.ts` (Vitest + JSDOM):

```ts
import { describe, it, expect, vi } from "vitest";
import { buildBeacon } from "./gwgeo";
describe("gwgeo", () => {
  it("builds a session beacon with referrer + visitor id", () => {
    const b = buildBeacon("wk_123", { referrer: "https://chatgpt.com/", href: "https://acme.com/crm" });
    expect(b.write_key).toBe("wk_123");
    expect(b.type).toBe("session");
    expect(b.referrer).toContain("chatgpt.com");
    expect(b.visitor_id).toBeTruthy();
  });
});
```

- [ ] **2. Run → fail** (backend + `web/` pixel).
- [ ] **3. Implement:** `ingest_*` (tenant-scoped writes via T02 session), write-key resolution
  (HMAC(salt, tenant:brand) → opaque key; store/verify), the public router, and `gwgeo.ts`
  (`buildBeacon`, `init(writeKey)`, `identify(email)`, `track(type,payload)`, first-party cookie
  `visitor_id`, `navigator.sendBeacon`). Add a `web/` build step producing `web/public/gwgeo.js`.
- [ ] **4. Run → pass** (pytest + `vitest run web/pixel`); mypy clean on touched `common`.
- [ ] **5. Commit:** `feat(attribution): lead-capture pixel sdk + ingestion beacon`

## Acceptance
- `ingest_session`/`ingest_lead` persist tenant-scoped rows and link lead→latest session;
  `/lead-capture/collect` is public, write-key-scoped, write-only (never leaks tenant data), rejects
  bad keys; `gwgeo.ts` builds a correct beacon and unit-tests green; hermetic.
