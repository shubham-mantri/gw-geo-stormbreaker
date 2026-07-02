from gw_geo.common.models import ContentDraft
from gw_geo.content.publish.metadata import build_jsonld, freshness_meta


def _draft():
    return ContentDraft(id="c1", tenant_id="t1", brand_id="b1", title="Best CRM",
                        body_markdown="## Q\nA")


def test_freshness_fields():
    m = freshness_meta("2026-07-01", "2026-07-02")
    assert m["datePublished"] == "2026-07-01" and m["dateModified"] == "2026-07-02"


def test_jsonld_has_schema_type_and_dates():
    ld = build_jsonld(_draft(), published="2026-07-01", modified="2026-07-02")
    assert ld["@context"] == "https://schema.org"
    assert ld["@type"] in {"Article", "FAQPage"}
    assert ld["headline"] == "Best CRM"
    assert ld["dateModified"] == "2026-07-02"


# --- supplementary coverage for the FAQPage-vs-Article heuristic (left open by the task spec) ---


def test_jsonld_defaults_to_article_for_plain_body():
    draft = ContentDraft(id="c2", tenant_id="t1", brand_id="b1", title="Why Acme Wins",
                          body_markdown="## Overview\nAcme is the best CRM for mid-market teams.")
    ld = build_jsonld(draft, published="2026-07-01", modified="2026-07-02")
    assert ld["@type"] == "Article"
    assert ld["datePublished"] == "2026-07-01"
    assert "mainEntity" not in ld


def test_jsonld_is_faqpage_when_schema_hint_present():
    draft = ContentDraft(id="c3", tenant_id="t1", brand_id="b1", title="Acme FAQ",
                          body_markdown="Acme is a CRM.", schema_jsonld={"@type": "FAQPage"})
    ld = build_jsonld(draft, published="2026-07-01", modified="2026-07-02")
    assert ld["@type"] == "FAQPage"


def test_jsonld_is_faqpage_for_qa_shaped_headings_and_extracts_main_entity():
    draft = ContentDraft(
        id="c4", tenant_id="t1", brand_id="b1", title="Acme FAQ",
        body_markdown=(
            "## Q1: What is Acme?\nAcme is a CRM.\n\n"
            "## Q2: Is Acme SOC2 certified?\nYes, Acme is SOC2 certified."
        ),
    )
    ld = build_jsonld(draft, published="2026-07-01", modified="2026-07-02")
    assert ld["@type"] == "FAQPage"
    assert [q["name"] for q in ld["mainEntity"]] == ["Q1: What is Acme?", "Q2: Is Acme SOC2 certified?"]
    assert ld["mainEntity"][0]["acceptedAnswer"]["text"] == "Acme is a CRM."


def test_jsonld_is_faqpage_for_literal_faq_mention_without_qa_headings():
    draft = ContentDraft(id="c5", tenant_id="t1", brand_id="b1", title="Acme FAQ",
                          body_markdown="## Frequently Asked Questions\nAcme is a CRM.")
    ld = build_jsonld(draft, published="2026-07-01", modified="2026-07-02")
    assert ld["@type"] == "FAQPage"
    assert "mainEntity" not in ld  # no question-shaped heading to extract from
