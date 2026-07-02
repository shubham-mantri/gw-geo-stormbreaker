# M2-T11 — CRM integration (HubSpot / Salesforce)

**Depends on:** T05 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Enrich `lead` rows with CRM deal stage + value so the pipeline view reports **real revenue**
(TRD §6 integrations, m2-design §5). One `Integration` interface, two connectors, HTTP `respx`-mocked;
secrets from config/SSM (never in repo). Connection state persists in the `integration` table.

**Files:**
- Create: `src/gw_geo/attribution/integrations/__init__.py`,
  `src/gw_geo/attribution/integrations/base.py`,
  `src/gw_geo/attribution/integrations/crm.py`
- Test: `tests/attribution/integrations/test_crm.py`,
  `tests/fixtures/crm/hubspot_deals.json`

## Interface

```python
# base.py
class Integration(Protocol):
    kind: str
    def connect(self, session, *, tenant_id: str, config: dict) -> dict: ...    # -> {"status": ...}
    async def sync(self, session, *, tenant_id: str, brand_id: str) -> int: ... # -> leads enriched

# crm.py
class HubSpotIntegration:
    kind = "hubspot"
    def __init__(self, settings, client: "httpx.AsyncClient | None" = None) -> None: ...
    def connect(self, session, *, tenant_id, config) -> dict: ...   # persist integration row
    async def sync(self, session, *, tenant_id, brand_id) -> int: ...
        # GET deals -> match by contact email to Lead.email -> set Lead.crm_stage, Lead.value_usd

class SalesforceIntegration:  # kind = "salesforce" — same shape
    ...
```

## Steps
- [ ] **1. Failing test** `tests/attribution/integrations/test_crm.py` (`respx`):

```python
import httpx, respx, pytest
from gw_geo.attribution.integrations.crm import HubSpotIntegration

@respx.mock
async def test_sync_enriches_lead(seeded_lead, settings):     # Lead(email="a@x.com") for t1/b1
    respx.get(url__regex=r"https://api\.hubapi\.com/crm/v3/objects/deals.*").mock(
        return_value=httpx.Response(200, json={"results":[
            {"properties":{"dealstage":"closedwon","amount":"5000","email":"a@x.com"}}]}))
    integ = HubSpotIntegration(settings, client=httpx.AsyncClient())
    n = await integ.sync(seeded_lead, tenant_id="t1", brand_id="b1")
    assert n == 1
    from gw_geo.common.db import Lead
    lead = next(l for l in seeded_lead.query(Lead).all() if l.email == "a@x.com")
    assert lead.crm_stage == "closedwon" and lead.value_usd == 5000.0

def test_connect_persists_integration_row(seeded_session, settings):
    out = HubSpotIntegration(settings).connect(seeded_session, tenant_id="t1",
        config={"access_token_ref": "ssm://hubspot/t1"})
    assert out["status"] in ("connected", "pending")
    from gw_geo.common.db import Integration
    assert seeded_session.query(Integration).filter_by(kind="hubspot").count() == 1
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** both connectors (bearer from `config`/SSM ref, `httpx.AsyncClient` injected),
  email-match enrichment (tenant-scoped `Lead` updates), and `connect` persisting an `integration`
  row (status, config_ref — never raw creds). Record the sanitized fixture under `tests/fixtures/crm/`.
- [ ] **4. Run → pass**; mypy clean on touched `common`.
- [ ] **5. Commit:** `feat(attribution): hubspot + salesforce crm integration`

## Acceptance
- `sync` matches CRM deals to leads by email and writes `crm_stage`+`value_usd` (tenant-scoped);
  `connect` persists an `integration` row without raw secrets; no live network (respx); mypy clean.
