from gw_geo.common.models import VisibilitySnapshot, Sentiment, AnswerExtraction

def test_snapshot_roundtrips():
    s = VisibilitySnapshot(brand_id="b1", engine="perplexity", geo="us", persona=None,
        date="2026-07-02", mention_rate=0.4, citation_rate=0.25, avg_position=2.0,
        sentiment_score=0.5, share_of_voice=0.33, n_samples=10, ci_low=0.2, ci_high=0.6)
    assert VisibilitySnapshot.model_validate_json(s.model_dump_json()).n_samples == 10

def test_extraction_requires_sentiment_enum():
    e = AnswerExtraction(probe_run_id="p1", brand_mentioned=True, position=1,
        sentiment=Sentiment.POSITIVE, cited_urls=[])
    assert e.sentiment == "positive"
