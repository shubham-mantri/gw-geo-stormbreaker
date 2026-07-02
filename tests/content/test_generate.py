from gw_geo.common.models import (
    Brand,
    ContentGap,
    ContentStatus,
    Fact,
    FeatureFactor,
    RankingReport,
)
from gw_geo.content.generate import build_generation_prompt, generate_draft

BRAND = Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com")
FACTS = [
    Fact(id="f1", brand_id="b1", text="Acme is SOC2 Type II certified", category="certification"),
    Fact(id="f2", brand_id="b1", text="Plans start at $29/mo", category="pricing"),
]
PROFILE = RankingReport(
    engine="perplexity",
    factors=[FeatureFactor(name="has_schema", weight=0.5, direction="positive", explanation="")],
    gaps=[
        ContentGap(
            engine="perplexity", factor="info_density", current_value=1.0, target_value=3.0
        )
    ],
)


class StubLLM:
    def __init__(self):
        self.seen_prompt = None

    def complete(self, *, system, prompt, schema=None):
        self.seen_prompt = prompt
        return {
            "title": "Best CRM for Startups",
            "body_markdown": "## Answer\nAcme is SOC2 Type II certified. Plans start at $29/mo.",
            "schema_jsonld": {"@type": "FAQPage"},
        }


def test_prompt_includes_facts_and_gaps():
    p = build_generation_prompt(
        brand=BRAND,
        prompt_text="best crm",
        facts=FACTS,
        feature_profile=PROFILE,
        intent_cluster="evaluation",
    )
    assert "SOC2 Type II" in p and "$29/mo" in p
    assert "info_density" in p  # gap surfaced to shape the draft


def test_generate_draft_grounds_and_stamps():
    llm = StubLLM()
    d = generate_draft(
        brand=BRAND,
        prompt_text="best crm",
        facts=FACTS,
        feature_profile=PROFILE,
        llm=llm,
        target_engine="perplexity",
        intent_cluster="evaluation",
        id_fn=lambda: "c1",
    )
    assert d.id == "c1" and d.tenant_id == "t1" and d.brand_id == "b1"
    assert d.status == ContentStatus.DRAFT
    assert d.grounded_fact_ids == ["f1", "f2"]
    assert d.schema_jsonld == {"@type": "FAQPage"} and "SOC2" in d.body_markdown
    assert "SOC2 Type II" in llm.seen_prompt  # generation was actually grounded
