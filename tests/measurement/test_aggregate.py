from gw_geo.common.models import AnswerExtraction, Sentiment
from gw_geo.measurement.aggregate import aggregate, wilson_ci


def _ext(m, pos=None, sent=Sentiment.NEUTRAL, cites=(), comps=()):
    return AnswerExtraction(
        probe_run_id="x",
        brand_mentioned=m,
        position=pos,
        sentiment=sent,
        cited_urls=list(cites),
        competitors_present=list(comps),
    )


def test_wilson_bounds_in_unit_interval():
    lo, hi = wilson_ci(4, 10)
    assert 0 <= lo <= 0.4 <= hi <= 1


def test_aggregate_rates_and_ci():
    exts = [
        _ext(True, 1, Sentiment.POSITIVE, ["https://a"], ["Acme"]),
        _ext(False),
        _ext(True, 3, Sentiment.NEUTRAL, [], ["Acme", "Beta"]),
        _ext(False),
    ]
    s = aggregate(
        exts, brand_id="b1", engine="perplexity", geo="us", persona=None, date="2026-07-02"
    )
    assert s.n_samples == 4 and s.mention_rate == 0.5 and s.citation_rate == 0.25
    assert s.ci_low < s.mention_rate < s.ci_high
    assert 0 <= s.share_of_voice <= 1 and s.avg_position == 2.0


# --- Additional coverage beyond the spec's verbatim tests -----------------------------------


def test_wilson_ci_property_bounds():
    """CI always sits inside [0, 1] and always brackets the observed rate."""
    for n in range(1, 25):
        for successes in range(0, n + 1):
            lo, hi = wilson_ci(successes, n)
            rate = successes / n
            assert 0.0 <= lo <= hi <= 1.0
            assert lo <= rate <= hi


def test_wilson_ci_guards_zero_n():
    lo, hi = wilson_ci(0, 0)
    assert 0.0 <= lo <= hi <= 1.0


def test_aggregate_guards_empty_extractions():
    s = aggregate([], brand_id="b1", engine="perplexity", geo="us", persona=None, date="2026-07-02")
    assert s.n_samples == 0
    assert s.mention_rate == 0.0
    assert s.citation_rate == 0.0
    assert s.avg_position is None
    assert s.sentiment_score == 0.0
    assert s.share_of_voice == 0.0


def test_aggregate_sentiment_score_averages_only_mentions():
    exts = [
        _ext(True, 1, Sentiment.POSITIVE),
        _ext(True, 2, Sentiment.NEGATIVE),
        _ext(False, None, Sentiment.NEGATIVE),
    ]
    s = aggregate(
        exts, brand_id="b1", engine="openai", geo="us", persona=None, date="2026-07-02"
    )
    assert s.sentiment_score == 0.0
