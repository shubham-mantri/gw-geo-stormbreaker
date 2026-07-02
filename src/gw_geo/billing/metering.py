"""Usage metering (m4-design §4.1): record billable usage per tenant and roll it up per period.

Probing dominates cost (TRD §7/§8), so `UsageKind.PROBE` events are the highest-volume kind
recorded here; content generations (M3) and seeding placements (M4) round out the billable
surface. Every subsystem that does billable work calls `record_usage` to stage one `UsageEvent`
row; `meter_period` rolls those events up per `UsageKind` for a tenant over a half-open
`[period_start, period_end)` window, feeding `billing/pricing.py`'s `compute_invoice` (T09).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from gw_geo.common import db


class UsageKind(StrEnum):
    PROBE = "probe"
    GENERATION = "generation"
    SEEDING_PLACEMENT = "seeding_placement"


# Billing unit per kind (m4-design §4.1/§4.4): each is priced per-unit via `PricingPlan.usage_rates`
# (billing/pricing.py, T09) -- e.g. $/call for probes, $/placement for seeding placements.
_UNIT_BY_KIND: dict[UsageKind, str] = {
    UsageKind.PROBE: "call",
    UsageKind.GENERATION: "generation",
    UsageKind.SEEDING_PLACEMENT: "placement",
}


def _parse_ts(value: str) -> datetime:
    """Parse a date-only (`2026-06-05`) or full ISO-8601 (optionally `Z`-suffixed) string.

    Aware datetimes are normalized to UTC and returned naive, so every timestamp this module
    writes (`record_usage`) or filters by (`meter_period`) is directly comparable regardless of
    whether the caller supplied an offset (mirrors `ranking/features.py::_parse_date`).
    """
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def record_usage(
    session: Session,
    *,
    tenant_id: str,
    brand_id: str | None,
    kind: UsageKind,
    quantity: float,
    ts: str,
    source_ref: str | None = None,
) -> None:
    """Stage one billable `UsageEvent` row for insert (m4-design §4.1).

    `unit` is inferred from `kind`. Does not commit -- the caller controls the transaction
    boundary (see `tests/billing/test_metering.py`, which batches several calls before one
    `session.commit()`).
    """
    session.add(
        db.UsageEvent(
            id=uuid4().hex,
            tenant_id=tenant_id,
            brand_id=brand_id,
            kind=kind.value,
            quantity=quantity,
            unit=_UNIT_BY_KIND[kind],
            ts=_parse_ts(ts),
            source_ref=source_ref,
        )
    )


class UsageSummary(BaseModel):
    tenant_id: str
    period_start: str
    period_end: str
    by_kind: dict[str, float] = Field(default_factory=dict)


def meter_period(
    session: Session, *, tenant_id: str, period_start: str, period_end: str
) -> UsageSummary:
    """Sum `usage_event.quantity` by `kind` for `tenant_id` within the half-open
    `[period_start, period_end)` window (m4-design §4.1).
    """
    start = _parse_ts(period_start)
    end = _parse_ts(period_end)
    rows = (
        session.query(db.UsageEvent)
        .filter(
            db.UsageEvent.tenant_id == tenant_id,
            db.UsageEvent.ts >= start,
            db.UsageEvent.ts < end,
        )
        .all()
    )

    by_kind: dict[str, float] = {}
    for row in rows:
        by_kind[row.kind] = by_kind.get(row.kind, 0.0) + row.quantity

    return UsageSummary(
        tenant_id=tenant_id, period_start=period_start, period_end=period_end, by_kind=by_kind
    )
