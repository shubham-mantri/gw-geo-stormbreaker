"""Tests for the local billing period-close job (`billing.trigger.run_billing_close_job`, M5).

Hermetic (TRD §12): `get_settings` is patched to a file-backed SQLite (FK enforcement ON via the
suite conftest), so the job opens/closes its own real `Session` against a seeded DB -- no cloud,
no EventBridge, no live network. The job composes `_load_plan` + `PipelineAttributionSource` +
the (deps-injected, pure) close-billing core, persists a `draft` invoice, and is idempotent per
`(tenant, period)`. It never finalizes/sends -- a human does that.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.billing import trigger
from gw_geo.billing.metering import UsageKind, record_usage
from gw_geo.common.config import Settings
from gw_geo.common.db import (
    AttributionLink,
    Base,
    BillingAccount,
    BillingInvoice,
    Brand,
    Lead,
    Tenant,
)

_PERIOD_START = "2026-06-01"
_PERIOD_END = "2026-07-01"


def _seed_db(url: str) -> None:
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Tenant(id="t1", name="t1", sampling_budget_daily=100.0))
        s.add(Brand(id="b1", tenant_id="t1", name="b1", domain="b1.com"))
        # growth plan: $500 base + $0.01/probe. RaaS off (default) -> attributed feeds no $.
        s.add(BillingAccount(id="ba1", tenant_id="t1", plan="growth", base_fee=500.0,
                             usage_rates={"probe": 0.01}))
        s.commit()
        record_usage(s, tenant_id="t1", brand_id="b1", kind=UsageKind.PROBE,
                     quantity=1000, ts="2026-06-10")
        s.commit()
        ts = datetime(2026, 6, 15, tzinfo=timezone.utc)
        s.add(Lead(id="l1", tenant_id="t1", brand_id="b1", visitor_id="v1", value_usd=100.0, ts=ts))
        s.commit()
        s.add(AttributionLink(id="al1", tenant_id="t1", brand_id="b1", lead_id="l1",
                              engine="perplexity", method="direct", confidence="high"))
        s.commit()


def test_run_billing_close_job_persists_draft_invoice(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'billing.db'}"
    _seed_db(url)
    monkeypatch.setattr(trigger, "get_settings", lambda: Settings(database_url=url))

    out = trigger.run_billing_close_job(
        tenant_id="t1", period_start=_PERIOD_START, period_end=_PERIOD_END
    )

    assert out["status"] == "draft"  # never finalized/sent
    assert out["total"] == 510.0  # 500 base + 1000 * 0.01 probe (RaaS off)

    with Session(create_engine(url)) as s:
        invoices = s.query(BillingInvoice).all()
        assert len(invoices) == 1
        inv = invoices[0]
        assert inv.status == "draft"
        assert inv.total == 510.0
        # PipelineAttributionSource wired: attributed leads/pipeline are populated (RaaS-safe).
        assert inv.attributed_leads == 1
        assert inv.attributed_pipeline_usd == 100.0


def test_run_billing_close_job_is_idempotent_per_period(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'billing.db'}"
    _seed_db(url)
    monkeypatch.setattr(trigger, "get_settings", lambda: Settings(database_url=url))

    out1 = trigger.run_billing_close_job(
        tenant_id="t1", period_start=_PERIOD_START, period_end=_PERIOD_END
    )
    out2 = trigger.run_billing_close_job(
        tenant_id="t1", period_start=_PERIOD_START, period_end=_PERIOD_END
    )

    assert out1["invoice_id"] == out2["invoice_id"]
    with Session(create_engine(url)) as s:
        assert s.query(BillingInvoice).count() == 1  # no second draft for the same period
