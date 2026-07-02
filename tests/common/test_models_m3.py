from gw_geo.common.models import (FeatureVector, ContentDraft, ContentStatus,
                                  GuardrailReport, Opportunity, BanditArm, SourceType)

def _fv(**kw):
    base = dict(structure_score=0.5, info_density=3.0, freshness_days=10.0, domain_authority=0.6,
                corroboration_count=4, embedding_similarity=0.8, has_schema=True, has_faq=False,
                table_count=2)
    base.update(kw); return FeatureVector(**base)

def test_feature_vector_as_list_is_ordered():
    fv = _fv()
    names = ["info_density", "domain_authority", "corroboration_count"]
    assert fv.as_list(names) == [3.0, 0.6, 4.0]

def test_draft_defaults_to_draft_status():
    d = ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="T", body_markdown="x")
    assert d.status == ContentStatus.DRAFT and d.grounded_fact_ids == []

def test_guardrail_and_opportunity_and_arm():
    g = GuardrailReport(originality_ok=True, originality_score=0.1, claims_ok=False,
                        unverified_claims=["revenue tripled"], brand_voice_ok=True,
                        brand_voice_score=0.9, passed=False)
    assert g.passed is False and "revenue tripled" in g.unverified_claims
    o = Opportunity(id="o1", tenant_id="t1", brand_id="b1", title="t", rationale="r",
                    engine="gemini", est_impact=0.7, source_gap="absence")
    assert o.status == "open"
    a = BanditArm(id="a1", tenant_id="t1", brand_id="b1", content_variant="v1",
                  channel=SourceType.REDDIT)
    assert a.alpha == 1.0 and a.channel == SourceType.REDDIT
