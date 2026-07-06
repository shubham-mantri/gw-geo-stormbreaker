"""`AttributionSource` adapter over M2's `attribution.pipeline_view` (m4-design §5, M5 live wiring).

`billing/pricing.py` consumes attributed results through the injected `AttributionSource` protocol
(`attributed_results(*, tenant_id, brand_id, period_start, period_end) -> AttributedResults`).
`handlers/close_billing.py` shipped a `_NullAttributionSource` placeholder (zero leads/pipeline)
because M2's `pipeline_view` answers a *brand-scoped* question (a mandatory `brand_id`, and a
"touched" lead count) that did not line up with billing's tenant-wide `AttributedResults`-shaped
call. This is the documented follow-on that closes that gap.

`PipelineAttributionSource` reconciles the two shapes:

* **tenant-wide fan-out.** Billing calls with `brand_id=None` (a tenant-level invoice). This adapter
  enumerates the tenant's brands (`TenantScopedSession(session, tenant_id).query(Brand)`), calls
  `pipeline_view` once per brand for the period, and sums the results. A specific `brand_id` scopes
  to that one brand instead.
* **strict `attributed` dollars, not `influenced`.** `attributed_pipeline_usd` sums each brand
  view's `"attributed"` figure -- the defensible subset whose strongest method is
  `direct`|`citation_linked` -- never `"influenced"` (which folds in low-confidence assisted
  credit). This is the honest, RaaS-safe number (PRD §13 anti-overclaim).
* **`attributed_leads` = touched-lead count.** `pipeline_view` exposes only a `"leads"` (touched)
  count, not an attributed-lead count, so that is what is summed here (the only count the view
  offers). Documented as the chosen semantics; RaaS defaults off (`PricingPlan.raas_enabled=False`)
  until attribution quality is proven, so this feeds nothing customer-facing by default.
* **half-open period alignment.** `meter_period` treats `[period_start, period_end)` as half-open
  (the period-end day belongs to the *next* period), while `pipeline_view`'s `until` is inclusive.
  This adapter converts by passing `until = period_end - 1 day`, so attributed leads cover exactly
  the same `[period_start, period_end)` window the usage meter does -- no double-counting a lead on
  a month boundary across two invoices.

Read-only: no writes, no network. `pipeline_view` scopes every read to `tenant_id` internally, so a
second tenant sees an all-zero view of the same database rather than another tenant's numbers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from gw_geo.attribution.pipeline import pipeline_view
from gw_geo.billing.pricing import AttributedResults
from gw_geo.common.db import Brand, TenantScopedSession


def _inclusive_until(period_end: str) -> str:
    """Convert a half-open `period_end` (exclusive, `YYYY-MM-DD`) to `pipeline_view`'s inclusive
    `until` (`period_end - 1 day`), so the attributed window matches `meter_period`'s
    `[period_start, period_end)`.
    """
    end = datetime.strptime(period_end, "%Y-%m-%d")
    return (end - timedelta(days=1)).date().isoformat()


class PipelineAttributionSource:
    """`AttributionSource` backed by `attribution.pipeline_view` (see module docstring)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def attributed_results(
        self, *, tenant_id: str, brand_id: str | None, period_start: str, period_end: str
    ) -> AttributedResults:
        """Sum `pipeline_view` over the tenant's brands (or one `brand_id`) for the period.

        Returns `AttributedResults(attributed_leads=Σ touched-leads, attributed_pipeline_usd=Σ
        strict-attributed $)`. `brand_id=None` (the billing default) fans out over every brand the
        tenant owns; a specific `brand_id` scopes to that brand alone.
        """
        until = _inclusive_until(period_end)

        if brand_id is not None:
            brand_ids = [brand_id]
        else:
            scoped = TenantScopedSession(self._session, tenant_id)
            brand_ids = [brand.id for brand in scoped.query(Brand).all()]

        total_leads = 0
        total_attributed = 0.0
        for bid in brand_ids:
            view = pipeline_view(
                self._session,
                tenant_id=tenant_id,
                brand_id=bid,
                since=period_start,
                until=until,
            )
            total_leads += int(view["leads"])
            total_attributed += float(view["attributed"])

        return AttributedResults(
            attributed_leads=total_leads, attributed_pipeline_usd=total_attributed
        )


__all__ = ["PipelineAttributionSource"]
