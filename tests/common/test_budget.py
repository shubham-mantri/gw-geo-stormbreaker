"""Tests for the per-tenant cost governor (TRD §7, docs/tasks/M0-T05-cost-governor.md)."""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.budget import BudgetExceeded, CostGovernor
from gw_geo.common.db import Base, Brand, ProbeRun, Prompt, Tenant


@pytest.fixture
def seeded_session() -> Iterator[Session]:
    """In-memory SQLite session seeded with tenant `t1` (`sampling_budget_daily=1.0`) and
    `ProbeRun` rows for `t1` totalling 0.30 spent today (UTC).

    Also seeds a same-day row for a *different* tenant (`t2`) and a *yesterday* row for `t1`,
    each with a cost (10.0) large enough to blow the budget if the tenant/day filter in
    `CostGovernor.spent_today` were broken -- proving the filter via the two spec tests below
    rather than via extra assertions.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    session.add(Tenant(id="t1", name="Acme", sampling_budget_daily=1.0))
    session.add(Tenant(id="t2", name="Globex", sampling_budget_daily=5.0))
    # ProbeRun.prompt_id -> prompt.id is a FK; seed the shared prompt (and its brand) that all
    # four ProbeRun rows below reference, so they are insertable under FK enforcement.
    session.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com"))
    session.add(Prompt(id="prompt1", tenant_id="t1", brand_id="b1", text="q"))
    session.add_all(
        [
            ProbeRun(
                id="pr1",
                tenant_id="t1",
                prompt_id="prompt1",
                engine="perplexity",
                geo="us",
                ts=now,
                status="ok",
                cost_usd=0.10,
            ),
            ProbeRun(
                id="pr2",
                tenant_id="t1",
                prompt_id="prompt1",
                engine="openai",
                geo="us",
                ts=now,
                status="ok",
                cost_usd=0.20,
            ),
            # Yesterday, tenant t1: must NOT count toward today's spend.
            ProbeRun(
                id="pr3",
                tenant_id="t1",
                prompt_id="prompt1",
                engine="perplexity",
                geo="us",
                ts=yesterday,
                status="ok",
                cost_usd=10.0,
            ),
            # Today, a different tenant: must NOT count toward t1's spend.
            ProbeRun(
                id="pr4",
                tenant_id="t2",
                prompt_id="prompt1",
                engine="perplexity",
                geo="us",
                ts=now,
                status="ok",
                cost_usd=10.0,
            ),
        ]
    )
    session.commit()

    yield session

    session.close()


def test_remaining_after_spend(seeded_session):
    gov = CostGovernor(seeded_session, "t1")   # 0.30 already spent today
    assert round(gov.remaining(), 2) == 0.70


def test_check_raises_when_over(seeded_session):
    gov = CostGovernor(seeded_session, "t1")
    with pytest.raises(BudgetExceeded):
        gov.check(estimated_cost=0.90)
