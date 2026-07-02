# M3-T16 — Guardrail runner + gate policy

**Depends on:** T07, T08, T15 · **Wave:** 2 (late) · **Suggested agent:** general-purpose

**Goal:** Aggregate the three guardrails (originality T07, brand-voice T08, claim-verification T15)
into a single `GuardrailReport` whose `passed` flag is the **hard precondition** for the approval gate
(T17) and publish (T22). `passed = originality_ok AND claims_ok AND brand_voice_ok`. This is the
single choke point that enforces "Athena's failure cannot happen here."

**Files:**
- Create: `src/gw_geo/content/guardrails/runner.py`
- Test: `tests/content/guardrails/test_runner.py`

## Interface

```python
from dataclasses import dataclass
from gw_geo.common.models import ContentDraft, GuardrailReport

@dataclass
class GuardrailThresholds:
    originality: float = 0.25
    claim_sim: float = 0.8
    brand_voice: float = 0.7

def run_guardrails(draft: ContentDraft, *, kb, corpus, extractor, voice_scorer,
                   voice_profile: dict, thresholds: GuardrailThresholds | None = None
                   ) -> GuardrailReport: ...
```

## Steps
- [ ] **1. Failing test** `tests/content/guardrails/test_runner.py`:

```python
from gw_geo.common.models import ContentDraft
from gw_geo.content.guardrails.runner import run_guardrails, GuardrailThresholds

def _draft():
    return ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="T",
                        body_markdown="Acme is soc2 certified. price starts at $29.")

# minimal fakes (reuse the KB/store/embedder fakes from test_claims via imports if desired)
class CleanCorpus:                       # nothing similar → original
    def search(self, text, *, top_k=5): return []
class DupCorpus:
    def search(self, text, *, top_k=5): return [("https://o", _draft().body_markdown)]
class GroundedExtractor:
    def extract_claims(self, text): return ["Acme is soc2 certified"]
class UngroundedExtractor:
    def extract_claims(self, text): return ["Acme revenue tripled"]
class GoodVoice:
    def score(self, text, profile): return {"score": 0.95, "violations": []}
class BadVoice:
    def score(self, text, profile): return {"score": 0.3, "violations": ["too salesy"]}

def _kb():
    from gw_geo.common.models import Fact
    from gw_geo.content.kb import KnowledgeBase
    from tests.content.guardrails.test_claims import WordEmbedder, FakeStore
    kb = KnowledgeBase(brand_id="b1", store=FakeStore(), embedder=WordEmbedder())
    kb.add_fact(Fact(id="f1", brand_id="b1", text="Acme is soc2 certified", category="certification"))
    return kb

def test_all_pass():
    r = run_guardrails(_draft(), kb=_kb(), corpus=CleanCorpus(), extractor=GroundedExtractor(),
                       voice_scorer=GoodVoice(), voice_profile={})
    assert r.originality_ok and r.claims_ok and r.brand_voice_ok and r.passed is True

def test_any_failure_blocks():
    # plagiarism fails → passed False even if the rest pass
    r = run_guardrails(_draft(), kb=_kb(), corpus=DupCorpus(), extractor=GroundedExtractor(),
                       voice_scorer=GoodVoice(), voice_profile={})
    assert r.originality_ok is False and r.passed is False

def test_ungrounded_claim_blocks():
    r = run_guardrails(_draft(), kb=_kb(), corpus=CleanCorpus(), extractor=UngroundedExtractor(),
                       voice_scorer=GoodVoice(), voice_profile={})
    assert r.claims_ok is False and r.passed is False and "Acme revenue tripled" in r.unverified_claims
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `runner.py` calling `check_originality`, `verify_claims`, `check_brand_voice`
  with thresholds (from `GuardrailThresholds`, defaults from config T01), composing a `GuardrailReport`
  with `passed = AND of the three`.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(content): guardrail runner + gate policy (passed = AND)`

## Acceptance
- `run_guardrails` returns a complete `GuardrailReport`; `passed` is True only when all three pass; any
  single failure (plagiarism, ungrounded claim, off-voice) forces `passed=False`.
