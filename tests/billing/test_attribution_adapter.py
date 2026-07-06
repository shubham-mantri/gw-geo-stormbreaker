"""Tests for `billing.attribution_adapter.PipelineAttributionSource` (M5 attribution->billing wiring).

Hermetic (TRD §12): in-memory SQLite with FK enforcement ON; the adapter composes the *real*
`attribution.pipeline_view` over seeded leads + attribution links -- no live network/LLM. Parents
are seeded before children under FK enforcement (Tenant -> Brand -> Lead -> AttributionLink).

The adapter fans out over the tenant's brands, calls `pipeline_view` per brand for the billing
period, and sums into `AttributedResults(attributed_leads=Σ touched-leads, attributed_pipeline_usd=
Σ strict-attributed $)` -- strict `attributed` (direct|citation_linked), never `influenced`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.billing.attribution_adapter import PipelineAttributionSource
from gw_geo.billing.pricing import AttributedResults
from gw_geo.common.db import AttributionLink, Base, Brand, Lead, Tenant

_PERIOD_START = "2026-06-01"
_PERIOD_END = "2026-07-01"  # half-open billing period end (exclusive), like meter_period


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(session: Session) -> None:
    # Tenants + brands first (FK parents).
    session.add(Tenant(id="t1", name="t1", sampling_budget_daily=100.0))
    session.add(Tenant(id="t2", name="t2", sampling_budget_daily=100.0))
    session.add(Brand(id="b1", tenant_id="t1", name="b1", domain="b1.com"))
    session.add(Brand(id="b2", tenant_id="t1", name="b2", domain="b2.com"))
    session.add(Brand(id="b3", tenant_id="t2", name="b3", domain="b3.com"))
    session.commit()

    ts = datetime(2026, 6, 15, tzinfo=timezone.utc)
    # b1: one direct-attributed lead ($100). b2: one citation_linked lead ($250).
    session.add(Lead(id="l1", tenant_id="t1", brand_id="b1", visitor_id="v1", value_usd=100.0, ts=ts))
    session.add(Lead(id="l2", tenant_id="t1", brand_id="b2", visitor_id="v2", value_usd=250.0, ts=ts))
    # t2's lead must never be counted for t1.
    session.add(Lead(id="l3", tenant_id="t2", brand_id="b3", visitor_id="v3", value_usd=999.0, ts=ts))
    session.commit()

    session.add(AttributionLink(id="al1", tenant_id="t1", brand_id="b1", lead_id="l1",
                                engine="perplexity", method="direct", confidence="high"))
    session.add(AttributionLink(id="al2", tenant_id="t1", brand_id="b2", lead_id="l2",
                                engine="openai", method="citation_linked", confidence="medium"))
    session.add(AttributionLink(id="al3", tenant_id="t2", brand_id="b3", lead_id="l3",
                                engine="perplexity", method="direct", confidence="high"))
    session.commit()


def test_fans_out_over_tenant_brands_and_sums_attributed() -> None:
    session = _session()
    _seed(session)

    got = PipelineAttributionSource(session).attributed_results(
        tenant_id="t1", brand_id=None, period_start=_PERIOD_START, period_end=_PERIOD_END
    )

    assert isinstance(got, AttributedResults)
    assert got.attributed_leads == 2  # touched leads across b1 + b2
    assert got.attributed_pipeline_usd == 350.0  # 100 (direct) + 250 (citation_linked)


def test_scopes_to_tenant_no_cross_tenant_leakage() -> None:
    session = _session()
    _seed(session)

    got = PipelineAttributionSource(session).attributed_results(
        tenant_id="t2", brand_id=None, period_start=_PERIOD_START, period_end=_PERIOD_END
    )

    assert got.attributed_leads == 1
    assert got.attributed_pipeline_usd == 999.0  # only t2's own brand


def test_specific_brand_id_scopes_to_that_brand() -> None:
    session = _session()
    _seed(session)

    got = PipelineAttributionSource(session).attributed_results(
        tenant_id="t1", brand_id="b1", period_start=_PERIOD_START, period_end=_PERIOD_END
    )

    assert got.attributed_leads == 1
    assert got.attributed_pipeline_usd == 100.0  # b1 only


def test_tenant_with_no_brands_is_zero() -> None:
    session = _session()
    session.add(Tenant(id="empty", name="empty", sampling_budget_daily=100.0))
    session.commit()

    got = PipelineAttributionSource(session).attributed_results(
        tenant_id="empty", brand_id=None, period_start=_PERIOD_START, period_end=_PERIOD_END
    )

    assert got.attributed_leads == 0
    assert got.attributed_pipeline_usd == 0.0


def test_period_end_is_exclusive_excludes_next_period_first_day() -> None:
    # A lead on the period-end day (2026-07-01) belongs to the NEXT period: meter_period treats
    # [start, end) half-open, so the adapter converts pipeline_view's inclusive `until` to match.
    session = _session()
    session.add(Tenant(id="t1", name="t1", sampling_budget_daily=100.0))
    session.add(Brand(id="b1", tenant_id="t1", name="b1", domain="b1.com"))
    session.commit()
    boundary = datetime(2026, 7, 1, tzinfo=timezone.utc)
    session.add(Lead(id="l1", tenant_id="t1", brand_id="b1", visitor_id="v1",
                     value_usd=100.0, ts=boundary))
    session.commit()
    session.add(AttributionLink(id="al1", tenant_id="t1", brand_id="b1", lead_id="l1",
                                engine="perplexity", method="direct", confidence="high"))
    session.commit()

    got = PipelineAttributionSource(session).attributed_results(
        tenant_id="t1", brand_id=None, period_start=_PERIOD_START, period_end=_PERIOD_END
    )

    assert got.attributed_leads == 0  # the 2026-07-01 lead is out of the half-open period
    assert got.attributed_pipeline_usd == 0.0
