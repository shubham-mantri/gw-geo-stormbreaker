# M2-T07 — Attribution mechanism 2: citation-to-page linkage

**Depends on:** T05 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Mechanism 2 (TRD §6, PRD §6.2 #2): join AI-referred sessions to the **specific seeded page
the AI cited** by matching the session's normalized `landing_url` to a `citation.url` for the same
brand/engine. Upgrades attribution with the citation id (which answer/prompt drove the visit) →
`attribution_link(method="citation_linked")`.

**Files:**
- Create: `src/gw_geo/attribution/linkage.py`
- Test: `tests/attribution/test_linkage.py`

## Interface

```python
def link_citations(session, *, tenant_id: str, brand_id: str,
                   since: str, until: str) -> list[AttributionLink]: ...
    # 1. load tenant-scoped sessions in range (AI-referred, from mechanism 1 / referrer)
    # 2. normalize session.landing_url and match to a Citation.url (same tenant/brand[/engine])
    # 3. write attribution_link(method="citation_linked", confidence="high",
    #      citation_id=<match>, prompt_id=<citation.prompt_id>, engine=<citation.engine>)
```
Reuse the M0 URL-normalization helper (same one `parse.py` uses) so matching is consistent.

## Steps
- [ ] **1. Failing test** `tests/attribution/test_linkage.py`:

```python
from gw_geo.attribution.linkage import link_citations

def test_links_session_to_cited_page(seeded_citation_and_session):
    # fixture: Citation(url="https://acme.com/crm-guide", engine="perplexity", prompt_id="p1")
    #          Session(landing_url="https://acme.com/crm-guide?utm=x", referrer="https://perplexity.ai/")
    links = link_citations(seeded_citation_and_session, tenant_id="t1", brand_id="b1",
                          since="2026-06-01", until="2026-07-02")
    assert len(links) == 1
    lk = links[0]
    assert lk.method == "citation_linked"
    assert lk.prompt_id == "p1" and lk.engine == "perplexity"

def test_no_match_when_url_differs(seeded_unmatched):
    assert link_citations(seeded_unmatched, tenant_id="t1", brand_id="b1",
                          since="2026-06-01", until="2026-07-02") == []
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** normalized URL matching (strip query/fragment/trailing slash consistently with
  M0), tenant-scoped joins across `session` + `citation`, idempotent link upsert.
- [ ] **4. Run → pass**; mypy clean on touched `common`.
- [ ] **5. Commit:** `feat(attribution): citation-to-page linkage (mechanism 2)`

## Acceptance
- Matches session landing pages to cited URLs (normalization-consistent with M0), records
  `citation_id`+`prompt_id`+`engine` on a `citation_linked` link, returns `[]` on no match,
  tenant-scoped + idempotent; hermetic.
