import json
from pathlib import Path

from gw_geo.common.models import ProbeResult, Brand, Sentiment, SourceType
from gw_geo.measurement.parse import normalize_url, classify_source, parse


def test_normalize_strips_tracking():
    assert normalize_url("https://X.com/a/?utm_source=z#h") == "https://x.com/a"


def test_classify_source():
    assert classify_source("https://www.reddit.com/r/x") == SourceType.REDDIT
    assert classify_source("https://en.wikipedia.org/wiki/Y") == SourceType.WIKIPEDIA


class StubExtractor:
    def extract(self, answer_text, brand):
        return {"brand_mentioned": True, "position": 2, "sentiment": "positive",
                "competitors_present": ["Acme"]}


def test_parse_builds_extraction():
    r = ProbeResult(engine="perplexity", answer_text="...", cited_urls=["https://reddit.com/r/x"])
    e = parse(r, Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com"),
              StubExtractor(), probe_run_id="pr1")
    assert e.brand_mentioned and e.position == 2
    assert e.sentiment == Sentiment.POSITIVE
    assert SourceType.REDDIT in e.source_types


# --- Additional coverage beyond the spec's verbatim tests -----------------------------------

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "answers"


def test_normalize_url_preserves_non_utm_query_and_strips_trailing_slash():
    assert normalize_url("https://Foo.com/path/?q=1&utm_campaign=x") == "https://foo.com/path?q=1"
    assert normalize_url("https://foo.com") == "https://foo.com"


def test_domain_of_strips_www():
    from gw_geo.measurement.parse import domain_of

    assert domain_of("https://www.g2.com/products/foo") == "g2.com"
    assert domain_of("foo.com") == "foo.com"


def test_classify_source_review_sites_and_default():
    assert classify_source("https://www.g2.com/products/foo/reviews") == SourceType.REVIEW_SITE
    assert classify_source("https://www.capterra.com/p/foo") == SourceType.REVIEW_SITE
    assert classify_source("https://example-blog.com/post") == SourceType.OTHER


def test_parse_tags_own_site_and_normalizes_cited_urls():
    data = json.loads((FIXTURES_DIR / "perplexity_sample.json").read_text())
    result = ProbeResult(**data)
    brand = Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com")

    extraction = parse(result, brand, StubExtractor(), probe_run_id="pr-fixture")

    assert extraction.probe_run_id == "pr-fixture"
    assert extraction.cited_urls == [normalize_url(u) for u in result.cited_urls]
    assert len(extraction.source_types) == len(extraction.cited_urls)
    assert SourceType.OWN_SITE in extraction.source_types
    assert SourceType.WIKIPEDIA in extraction.source_types
    assert SourceType.REDDIT in extraction.source_types
    assert SourceType.REVIEW_SITE in extraction.source_types
    # utm_source must be stripped from the G2 citation
    assert not any("utm_" in u for u in extraction.cited_urls)
