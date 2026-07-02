"""Tests for ranking recommendations (M3-T12, `docs/m3-design.md` §2.4).

Reuses `tests.ranking.test_model`'s `FakeBackend`/`_fv` fixtures rather than redefining them, per
`docs/tasks/M3-T12-recommendations.md`.
"""

from __future__ import annotations

from gw_geo.common.models import FeatureFactor, LabeledExample, SourceType
from gw_geo.ranking.model import EngineRankingModel
from gw_geo.ranking.recommend import build_report, channel_recommendations, find_gaps
from tests.ranking.test_model import FakeBackend, _fv


def test_find_gaps_flags_below_target() -> None:
    factors = [
        FeatureFactor(name="info_density", weight=0.9, direction="positive", explanation="")
    ]
    cur = _fv(0.5)  # info_density=2.0 in the helper
    gaps = find_gaps(factors, cur, targets={"info_density": 3.0})
    assert len(gaps) == 1 and gaps[0].factor == "info_density"
    assert gaps[0].current_value == 2.0 and gaps[0].target_value == 3.0


def test_channel_recs_ranked_by_mix() -> None:
    recs = channel_recommendations(
        "perplexity", {SourceType.REDDIT: 0.7, SourceType.REVIEW_SITE: 0.2}
    )
    assert recs[0].channel == SourceType.REDDIT and recs[0].engine == "perplexity"
    assert recs[0].est_impact >= recs[1].est_impact


def test_build_report_assembles_all_three() -> None:
    m = EngineRankingModel("perplexity", FakeBackend())
    m.train(
        [
            LabeledExample(engine="perplexity", features=_fv(0.9), cited=True),
            LabeledExample(engine="perplexity", features=_fv(0.1), cited=False),
        ]
    )
    rep = build_report(
        m, _fv(0.1), {SourceType.REDDIT: 0.6}, targets={"structure_score": 0.8}
    )
    assert rep.engine == "perplexity"
    assert rep.factors and rep.channel_recommendations
    assert any(g.factor == "structure_score" for g in rep.gaps)
