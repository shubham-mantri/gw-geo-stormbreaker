"""Tests for `orchestration.retrainer.RankingRetrainer` (M5: real ranking retrain behind the
`Retrainer.retrain(*, engine)` seam that `RetrainTrigger` (T12) drives on a drift breach).

Hermetic (TRD §12): the ranking refresh is an injected fake `retrain_fn` (no live crawl / embed /
sklearn); the FeatureModel lookup runs over a shared in-memory SQLite via an injected
`session_factory`. Parents (Tenant -> Brand) seeded before the FeatureModel child under FK
enforcement.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from gw_geo.common.db import Base, Brand, DriftEvent, FeatureModel, Tenant
from gw_geo.common.models import RankingReport
from gw_geo.orchestration.retrain import RetrainTrigger
from gw_geo.orchestration.retrainer import RankingRetrainer

TENANT = "t1"
BRAND = "b1"


def _shared_engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Tenant(id=TENANT, name="t", sampling_budget_daily=100.0))
        s.add(Brand(id=BRAND, tenant_id=TENANT, name="b", domain="b.com"))
        s.commit()
    return eng


def test_retrain_reports_persisted_feature_model_id_and_metrics() -> None:
    eng = _shared_engine()

    def factory() -> Session:
        return Session(eng)

    calls: list[tuple[str, str, tuple[str, ...]]] = []

    def fake_retrain_fn(*, tenant_id, brand_id, engines):
        calls.append((tenant_id, brand_id, tuple(engines)))
        with factory() as s:
            s.add(FeatureModel(id="fm1", tenant_id=tenant_id, brand_id=brand_id, engine=engines[0],
                               model_type="gbt", metrics={"auc": 0.81, "n_examples": 10, "n_cited": 5}))
            s.commit()
        return {engines[0]: RankingReport(engine=engines[0])}

    r = RankingRetrainer(TENANT, BRAND, retrain_fn=fake_retrain_fn, session_factory=factory)
    out = r.retrain(engine="perplexity")

    assert out["model_ref"] == "fm1"  # the persisted FeatureModel id
    assert out["metrics"]["auc"] == 0.81
    assert calls == [(TENANT, BRAND, ("perplexity",))]  # bound tenant/brand, single-engine call


def test_retrain_is_noop_when_no_model_trained(caplog) -> None:
    eng = _shared_engine()

    def factory() -> Session:
        return Session(eng)

    def fake_retrain_fn(*, tenant_id, brand_id, engines):
        return {}  # e.g. brand missing / nothing to train

    r = RankingRetrainer(TENANT, BRAND, retrain_fn=fake_retrain_fn, session_factory=factory)
    with caplog.at_level(logging.WARNING):
        out = r.retrain(engine="perplexity")

    assert out["model_ref"].startswith("perplexity@")  # synthesized ref
    assert out["metrics"] == {}
    assert "no-op" in caplog.text  # clearly logged


def test_retrain_single_engine_degenerate_logs_but_reports_real_model(caplog) -> None:
    # <2 engines measured -> all-positive labels -> no "auc". Honest no-op: log it, but still
    # report the real (degenerate) artifact + its metrics rather than faking a discriminative model.
    eng = _shared_engine()

    def factory() -> Session:
        return Session(eng)

    def fake_retrain_fn(*, tenant_id, brand_id, engines):
        with factory() as s:
            s.add(FeatureModel(id="fm2", tenant_id=tenant_id, brand_id=brand_id, engine=engines[0],
                               model_type="gbt", metrics={"n_examples": 3, "n_cited": 3}))
            s.commit()
        return {engines[0]: RankingReport(engine=engines[0])}

    r = RankingRetrainer(TENANT, BRAND, retrain_fn=fake_retrain_fn, session_factory=factory)
    with caplog.at_level(logging.WARNING):
        out = r.retrain(engine="perplexity")

    assert out["model_ref"] == "fm2"  # honest: the real artifact
    assert "auc" not in out["metrics"]
    assert "no-op" in caplog.text


def test_retrainer_satisfies_protocol_via_retrain_trigger() -> None:
    # Plugged into the real RetrainTrigger, a breach yields a succeeded job carrying the model
    # ref + metrics -- proving RankingRetrainer satisfies the `Retrainer` protocol end-to-end.
    model_eng = _shared_engine()

    def factory() -> Session:
        return Session(model_eng)

    def fake_retrain_fn(*, tenant_id, brand_id, engines):
        with factory() as s:
            s.add(FeatureModel(id="fm3", tenant_id=tenant_id, brand_id=brand_id, engine=engines[0],
                               model_type="gbt", metrics={"auc": 0.7}))
            s.commit()
        return {engines[0]: RankingReport(engine=engines[0])}

    # A separate engine for the trigger's own session (drift_event + retrain_job) so the trigger's
    # long-lived transaction never overlaps the retrainer's short lookup sessions on one connection.
    trig_eng = create_engine("sqlite://")
    Base.metadata.create_all(trig_eng)
    with Session(trig_eng) as s:
        s.add(DriftEvent(id="d1", engine="perplexity", canary_id="c1", baseline_rate=0.6,
                         observed_rate=0.3, drop=0.3, breached=True, retrain_flag=True,
                         ts=datetime.now(timezone.utc)))
        s.commit()
        retrainer = RankingRetrainer(
            TENANT, BRAND, retrain_fn=fake_retrain_fn, session_factory=factory
        )
        job = RetrainTrigger(s, retrainer=retrainer).on_breach("d1")

    assert job.status == "succeeded"
    assert job.model_ref == "fm3"
    assert job.metrics_after["auc"] == 0.7
