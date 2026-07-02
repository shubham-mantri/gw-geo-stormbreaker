"""Aggregate: roll up N `AnswerExtraction`s into one `VisibilitySnapshot`.

This is where the TRD §3 non-determinism rule is enforced: every visibility metric is a
proportion with a sample size and a Wilson 95% CI, never a single answer treated as ground
truth. `wilson_ci` is a pure closed-form implementation (no `scipy`/`statsmodels` dependency
needed for the standard z=1.96 default).
"""

from __future__ import annotations

import math

from gw_geo.common.models import AnswerExtraction, Sentiment, VisibilitySnapshot

_SENTIMENT_SCORE: dict[Sentiment, float] = {
    Sentiment.POSITIVE: 1.0,
    Sentiment.NEUTRAL: 0.0,
    Sentiment.COMPARISON: 0.0,
    Sentiment.NEGATIVE: -1.0,
}


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion, clamped to `[0, 1]`.

    `z` defaults to 1.96 (95% confidence). Guarded for `n == 0`: with no samples there is no
    evidence at all, so this returns the maximally-uninformative `(0.0, 1.0)` rather than
    dividing by zero.
    """
    if n == 0:
        return (0.0, 1.0)

    phat = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = phat + z2 / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z2 / (4 * n)) / n)

    # successes == 0 / successes == n are mathematically exact fixed points (lower == 0,
    # upper == 1 respectively) but land on catastrophic cancellation in the general formula
    # (subtracting/adding two nearly-equal floats), so handle them exactly rather than via
    # the `max`/`min` clamp below picking up float noise like 2e-17.
    lower = 0.0 if successes == 0 else (center - margin) / denominator
    upper = 1.0 if successes == n else (center + margin) / denominator
    return (max(0.0, lower), min(1.0, upper))


def aggregate(
    extractions: list[AnswerExtraction],
    *,
    brand_id: str,
    engine: str,
    geo: str,
    persona: str | None,
    date: str,
) -> VisibilitySnapshot:
    """Roll up `extractions` for one (brand, engine, geo, persona, date) into a snapshot."""
    n = len(extractions)
    mentions = sum(1 for e in extractions if e.brand_mentioned)
    cited = sum(1 for e in extractions if e.cited_urls)

    mention_rate = mentions / n if n else 0.0
    citation_rate = cited / n if n else 0.0
    ci_low, ci_high = wilson_ci(mentions, n)

    positions = [e.position for e in extractions if e.position is not None]
    avg_position = sum(positions) / len(positions) if positions else None

    mentioned = [e for e in extractions if e.brand_mentioned]
    sentiment_score = (
        sum(_SENTIMENT_SCORE[e.sentiment] for e in mentioned) / len(mentioned)
        if mentioned
        else 0.0
    )

    total_competitor_mentions = sum(len(e.competitors_present) for e in extractions)
    denom = mentions + total_competitor_mentions
    share_of_voice = mentions / denom if denom else 0.0

    return VisibilitySnapshot(
        brand_id=brand_id,
        engine=engine,
        geo=geo,
        persona=persona,
        date=date,
        mention_rate=mention_rate,
        citation_rate=citation_rate,
        avg_position=avg_position,
        sentiment_score=sentiment_score,
        share_of_voice=share_of_voice,
        n_samples=n,
        ci_low=ci_low,
        ci_high=ci_high,
    )
