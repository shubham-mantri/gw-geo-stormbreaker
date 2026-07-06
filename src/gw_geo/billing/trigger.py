"""Local, in-process billing period-close job (M5 live wiring) -- the LOCAL analogue of
`handlers/close_billing.py`, with **no** cloud/Lambda/EventBridge anywhere.

`handlers/close_billing.py` is the scheduled (monthly EventBridge cron -> Lambda) period-close; its
production path used a `_NullAttributionSource` placeholder because no concrete `AttributionSource`
over M2's `pipeline_view` existed yet. `run_billing_close_job` is the local job both a CLI
(`close-billing`) and any future request path call: it opens (and always closes) its own `Session`
from `settings.database_url`, resolves the tenant's plan via the handler's own `_load_plan`, and
wires the now-real `PipelineAttributionSource` (M5) so the invoice carries actual attributed
leads/pipeline instead of zeros.

The pricing + idempotency logic is **not** re-implemented here: the job delegates to
`handlers.close_billing.handler` through its `deps` seam (a pure, cloud-free code path -- the same
seam its unit tests use), so metering, `compute_invoice`, the `(tenant, period)` idempotency guard,
and the `draft`-status persist all run exactly once, in one place. The invoice is persisted in
`"draft"` status and **never sent**: reviewing/finalizing an invoice before it is customer-facing is
a separate, deliberately human-gated step (mirroring `seeding.workflow`'s human-in-the-loop posture).

`get_settings` is imported by name so tests can patch `gw_geo.billing.trigger.get_settings` and keep
the job hermetic (a file-backed SQLite in place of a live database).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.billing.attribution_adapter import PipelineAttributionSource
from gw_geo.common.config import get_settings
from gw_geo.common.db import BillingInvoice
from gw_geo.handlers.close_billing import _DRAFT_STATUS, _load_plan
from gw_geo.handlers.close_billing import handler as _close_billing_handler

logger = logging.getLogger(__name__)


def run_billing_close_job(
    *, tenant_id: str, period_start: str, period_end: str
) -> dict[str, Any]:
    """Close (meter + price + persist a draft invoice for) one billing period, locally.

    `period_start`/`period_end` are `YYYY-MM-DD`, half-open `[period_start, period_end)`. Opens its
    own `Session`, resolves the plan (`_load_plan`), wires `PipelineAttributionSource`, and delegates
    to the deps-injected close-billing core -- idempotent per `(tenant_id, period_start, period_end)`:
    a re-run for an already-closed period returns the existing invoice rather than inserting a second
    draft. Persists `status="draft"` and never finalizes/sends. Returns `{"invoice_id", "total",
    "status"}` for the (new or pre-existing) invoice.
    """
    settings = get_settings()
    session = Session(create_engine(settings.database_url))
    try:
        plan = _load_plan(session, tenant_id)
        deps = {
            "session": session,
            "plan": plan,
            "attribution": PipelineAttributionSource(session),
        }
        out = _close_billing_handler(
            {"tenant_id": tenant_id, "period_start": period_start, "period_end": period_end},
            deps=deps,
        )
        body = out["body"]
        invoice = session.get(BillingInvoice, body["invoice_id"])
        status = invoice.status if invoice is not None else _DRAFT_STATUS

        logger.info(
            "billing close job done tenant_id=%s period=%s..%s invoice=%s total=%.2f status=%s",
            tenant_id,
            period_start,
            period_end,
            body["invoice_id"],
            body["total"],
            status,
        )
        return {"invoice_id": body["invoice_id"], "total": body["total"], "status": status}
    finally:
        session.close()


__all__ = ["run_billing_close_job"]
