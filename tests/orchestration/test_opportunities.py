"""Opportunities queue tests (M3-T19, `docs/tasks/M3-T19-opportunities-service.md`).

Hermetic (TRD §12): `build_opportunities` is a pure function over already-loaded
`VisibilitySnapshot`/`RankingReport` domain objects -- no DB/HTTP/LLM calls.
"""

from __future__ import annotations

from gw_geo.common.models import (
    Brand,
    ChannelRecommendation,
    RankingReport,
    SourceType,
    VisibilitySnapshot,
)
from gw_geo.orchestration.opportunities import build_opportunities

BRAND = Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com")


def _snap(engine: str, mention: float, sentiment: float = 0.0) -> VisibilitySnapshot:
    return VisibilitySnapshot(
        brand_id="b1",
        engine=engine,
        geo="us",
        persona=None,
        date="2026-07-02",
        mention_rate=mention,
        citation_rate=mention * 0.6,
        avg_position=3.0,
        sentiment_score=sentiment,
        share_of_voice=mention,
        n_samples=10,
        ci_low=max(0.0, mention - 0.1),
        ci_high=mention + 0.1,
    )


def test_absence_generates_ranked_opportunity() -> None:
    opps = build_opportunities(
        brand=BRAND,
        snapshots=[_snap("gemini", 0.02), _snap("perplexity", 0.55)],
        reports=[
            RankingReport(
                engine="gemini",
                channel_recommendations=[
                    ChannelRecommendation(
                        engine="gemini",
                        channel=SourceType.REDDIT,
                        rationale="gemini trusts reddit",
                        est_impact=0.9,
                    )
                ],
            )
        ],
        source_mix={},
        id_fn=lambda: "o1",
    )
    assert opps, "expected at least one opportunity"
    top = opps[0]
    assert top.tenant_id == "t1" and top.brand_id == "b1"
    assert top.engine == "gemini" and top.source_gap in {"absence", "source"}
    # ranked: absence on the low-mention engine outranks the healthy engine
    assert all(opps[i].est_impact >= opps[i + 1].est_impact for i in range(len(opps) - 1))


def test_sentiment_gap_surfaced() -> None:
    opps = build_opportunities(
        brand=BRAND,
        snapshots=[_snap("gemini", 0.4, sentiment=0.0)],
        reports=[],
        source_mix={},
        id_fn=lambda: "o1",
    )
    assert any(o.source_gap == "sentiment" for o in opps)
