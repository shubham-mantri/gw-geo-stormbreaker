# M3-T15 — Guardrail: claim-verification vs knowledge base

**Depends on:** T06 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** The **anti-hallucination guard** (PRD §6.4, §13): extract factual claims from a draft and
verify **each** against the brand KB. A claim is verified iff the KB grounds it above
`claim_sim_threshold` (config, default 0.8); any **unverified** claim → `claims_ok = False`. This is
what stops fabricated stats/claims from reaching publish. `ClaimExtractor` is **injected** (real impl =
LLM); no live calls in tests.

**Files:**
- Create: `src/gw_geo/content/guardrails/claims.py`
- Test: `tests/content/guardrails/test_claims.py`

## Interface

```python
from typing import Protocol
from gw_geo.content.kb import KnowledgeBase

class ClaimExtractor(Protocol):
    def extract_claims(self, text: str) -> list[str]: ...   # atomic factual claims

def verify_claims(draft_text: str, *, kb: KnowledgeBase, extractor: ClaimExtractor,
                  sim_threshold: float = 0.8) -> tuple[bool, list[str]]: ...
# returns (claims_ok, unverified_claims);  claims_ok = (unverified_claims == [])
# a claim is verified iff kb.ground(claim) yields a supporting fact with similarity >= sim_threshold
```

Note: `KnowledgeBase.ground` returns `Fact`s; T15 needs the *score* too. Add a
`KnowledgeBase.ground_scored(query, top_k) -> list[tuple[Fact, float]]` helper in `kb.py` (extends T06,
back-compatible) so claim-verification can threshold on similarity.

## Steps
- [ ] **1. Failing test** `tests/content/guardrails/test_claims.py`:

```python
from gw_geo.common.models import Fact
from gw_geo.content.kb import KnowledgeBase
from gw_geo.content.guardrails.claims import verify_claims

class WordEmbedder:
    VOCAB = ["soc2", "price", "uptime", "revenue"]
    def embed(self, text):
        t = text.lower(); return [1.0 if w in t else 0.0 for w in self.VOCAB]

class FakeStore:
    def __init__(self): self.rows = {}
    def upsert(self, id, vector, meta): self.rows[id] = (vector, meta)
    def query(self, vector, top_k):
        import math
        def cos(a, b):
            na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(x*x for x in b))
            return 0.0 if na == 0 or nb == 0 else sum(x*y for x, y in zip(a, b))/(na*nb)
        scored = [(i, cos(vector, v), m) for i, (v, m) in self.rows.items()]
        scored.sort(key=lambda r: r[1], reverse=True); return scored[:top_k]

def _kb():
    kb = KnowledgeBase(brand_id="b1", store=FakeStore(), embedder=WordEmbedder())
    kb.add_fact(Fact(id="f1", brand_id="b1", text="Acme is soc2 certified", category="certification"))
    kb.add_fact(Fact(id="f2", brand_id="b1", text="price starts at $29", category="pricing"))
    return kb

class StubExtractor:
    def __init__(self, claims): self._c = claims
    def extract_claims(self, text): return self._c

def test_grounded_claims_pass():
    ok, unver = verify_claims("...", kb=_kb(),
                              extractor=StubExtractor(["Acme is soc2 certified"]), sim_threshold=0.8)
    assert ok is True and unver == []

def test_fabricated_claim_flagged():
    # "revenue tripled" is NOT in the KB → unverified → claims_ok False (the Athena failure, prevented)
    ok, unver = verify_claims("...", kb=_kb(),
                              extractor=StubExtractor(["Acme revenue tripled last quarter"]),
                              sim_threshold=0.8)
    assert ok is False and "Acme revenue tripled last quarter" in unver

def test_mixed_claims_fail_closed():
    ok, unver = verify_claims("...", kb=_kb(),
        extractor=StubExtractor(["Acme is soc2 certified", "Acme revenue tripled last quarter"]),
        sim_threshold=0.8)
    assert ok is False and len(unver) == 1
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `kb.ground_scored` (T06 extension) + `claims.py`. Provide a real `LLMClaimExtractor`
  (injected LLM) alongside — **not** tested live.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(content): claim-verification guardrail vs knowledge base`

## Acceptance
- Every extracted claim is checked against the KB; grounded claims pass, ungrounded/fabricated claims
  are reported as unverified and force `claims_ok=False` (fail-closed); no live LLM calls.
