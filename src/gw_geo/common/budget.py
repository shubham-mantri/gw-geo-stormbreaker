"""Per-tenant daily sampling-budget guard (TRD §7).

Probing is the dominant cost in this system. Before spending on a probe batch, `CostGovernor`
checks a tenant's remaining daily sampling budget -- `tenant.sampling_budget_daily` minus
today's (UTC) `probe_run.cost_usd` sum -- and raises `BudgetExceeded` when a prospective spend
would exceed it. This guard is not optional (TRD §7).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gw_geo.common.db import ProbeRun, Tenant


class BudgetExceeded(Exception):
    """Raised when a prospective spend would exceed a tenant's remaining daily budget."""


class CostGovernor:
    """Enforces a tenant's daily sampling budget (`tenant.sampling_budget_daily`)."""

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self.tenant_id = tenant_id

    def spent_today(self) -> float:
        """Sum of `ProbeRun.cost_usd` for this tenant where `ts` falls on today's UTC date.

        The UTC day range is computed in Python (`[start, end)` = `[today 00:00 UTC, +1 day)`)
        so the comparison is a portable half-open range across SQLite (tests) and Postgres,
        rather than relying on a DB-specific "date of timestamp" function.
        """
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

        stmt = select(func.sum(ProbeRun.cost_usd)).where(
            ProbeRun.tenant_id == self.tenant_id,
            ProbeRun.ts >= start,
            ProbeRun.ts < end,
        )
        total = self._session.execute(stmt).scalar_one()
        return float(total) if total is not None else 0.0

    def _daily_budget(self) -> float:
        tenant = self._session.get(Tenant, self.tenant_id)
        if tenant is None:
            raise ValueError(f"unknown tenant_id={self.tenant_id!r}")
        return tenant.sampling_budget_daily

    def remaining(self) -> float:
        """Remaining daily budget: `tenant.sampling_budget_daily` minus `spent_today()`."""
        return self._daily_budget() - self.spent_today()

    def check(self, estimated_cost: float) -> None:
        """Raise `BudgetExceeded` if `estimated_cost` exceeds the tenant's remaining budget."""
        remaining = self.remaining()
        if estimated_cost > remaining:
            raise BudgetExceeded(
                f"tenant_id={self.tenant_id!r} estimated_cost={estimated_cost} exceeds "
                f"remaining daily sampling budget={remaining}"
            )

    def can_afford(self, estimated_cost: float) -> bool:
        """Return whether `estimated_cost` fits within the tenant's remaining budget."""
        return estimated_cost <= self.remaining()
