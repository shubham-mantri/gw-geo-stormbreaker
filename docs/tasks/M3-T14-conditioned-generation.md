# M3-T14 — Conditioned generation (grounded, feature-shaped)

**Depends on:** T06 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Generate on-brand content **conditioned** on the target engine's learned feature profile
(`RankingReport`) + intent cluster, and **grounded** in the brand KB (PRD §6.4). The LLM prompt carries
only KB facts; the returned `ContentDraft` records which facts grounded it (`grounded_fact_ids`) and
includes extraction-friendly JSON-LD. The `LLMClient` is **injected** — no live calls in tests.
(See the `claude-api` skill for the Anthropic Messages/JSON contract the real client uses.)

**Files:**
- Create: `src/gw_geo/content/generate.py`
- Test: `tests/content/test_generate.py`

## Interface

```python
from typing import Any, Protocol
from gw_geo.common.models import Brand, Fact, ContentDraft, RankingReport

class LLMClient(Protocol):
    def complete(self, *, system: str, prompt: str,
                 schema: dict[str, Any] | None = None) -> dict[str, Any]: ...
    # returns {"title": str, "body_markdown": str, "schema_jsonld": dict}

def build_generation_prompt(*, brand: Brand, prompt_text: str, facts: list[Fact],
                            feature_profile: RankingReport | None,
                            intent_cluster: str | None) -> str: ...
def generate_draft(*, brand: Brand, prompt_text: str, facts: list[Fact],
                   feature_profile: RankingReport | None, llm: LLMClient,
                   target_engine: str | None = None, intent_cluster: str | None = None,
                   id_fn=None) -> ContentDraft: ...
```

Rules: the prompt must include every fact's text (grounding) and the profile's gap factors (shaping);
`generate_draft` stamps `grounded_fact_ids = [f.id for f in facts]`, `tenant_id`/`brand_id` from `brand`,
status `DRAFT`; `id_fn` defaults to uuid4 (injected in tests).

## Steps
- [ ] **1. Failing test** `tests/content/test_generate.py`:

```python
from gw_geo.common.models import Brand, Fact, RankingReport, FeatureFactor, ContentGap, ContentStatus
from gw_geo.content.generate import build_generation_prompt, generate_draft

BRAND = Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com")
FACTS = [Fact(id="f1", brand_id="b1", text="Acme is SOC2 Type II certified", category="certification"),
         Fact(id="f2", brand_id="b1", text="Plans start at $29/mo", category="pricing")]
PROFILE = RankingReport(engine="perplexity",
                        factors=[FeatureFactor(name="has_schema", weight=0.5, direction="positive", explanation="")],
                        gaps=[ContentGap(engine="perplexity", factor="info_density",
                                        current_value=1.0, target_value=3.0)])

class StubLLM:
    def __init__(self): self.seen_prompt = None
    def complete(self, *, system, prompt, schema=None):
        self.seen_prompt = prompt
        return {"title": "Best CRM for Startups",
                "body_markdown": "## Answer\nAcme is SOC2 Type II certified. Plans start at $29/mo.",
                "schema_jsonld": {"@type": "FAQPage"}}

def test_prompt_includes_facts_and_gaps():
    p = build_generation_prompt(brand=BRAND, prompt_text="best crm", facts=FACTS,
                                feature_profile=PROFILE, intent_cluster="evaluation")
    assert "SOC2 Type II" in p and "$29/mo" in p
    assert "info_density" in p            # gap surfaced to shape the draft

def test_generate_draft_grounds_and_stamps():
    llm = StubLLM()
    d = generate_draft(brand=BRAND, prompt_text="best crm", facts=FACTS, feature_profile=PROFILE,
                       llm=llm, target_engine="perplexity", intent_cluster="evaluation",
                       id_fn=lambda: "c1")
    assert d.id == "c1" and d.tenant_id == "t1" and d.brand_id == "b1"
    assert d.status == ContentStatus.DRAFT
    assert d.grounded_fact_ids == ["f1", "f2"]
    assert d.schema_jsonld == {"@type": "FAQPage"} and "SOC2" in d.body_markdown
    assert "SOC2 Type II" in llm.seen_prompt     # generation was actually grounded
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `generate.py`. Provide a real `AnthropicLLMClient` (JSON mode) alongside —
  **not** tested live. Generation is grounded-only: no facts beyond those passed in.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(content): grounded, feature-conditioned generation`

## Acceptance
- The prompt carries KB facts + profile gaps; `generate_draft` returns a `DRAFT` `ContentDraft` scoped
  to the brand/tenant with `grounded_fact_ids` + JSON-LD; no live LLM calls.
