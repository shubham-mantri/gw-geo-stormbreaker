"""Local, session-based trigger for one measurement pass (W2 live wiring).

`run_measurement_job` is the local, in-process counterpart to `handlers/run_measurement.py`'s
Lambda handler: it wires the same real dependencies via `gw_geo.common.wiring.build_runtime`,
opens a fresh SQLAlchemy `Session` from `settings.database_url`, and drives the same
`gw_geo.measurement.runner.run_measurement` pipeline via `asyncio.run` -- but with **no** AWS /
Lambda / EventBridge anywhere. It is the single unit both request-path (the
`POST /brands/{id}/measure` FastAPI trigger, scheduled onto a `BackgroundTasks`) and the CLI
`schedule` loop call, so those two never diverge in how a local run is wired.

Being a plain sync function that owns its own event loop (`asyncio.run`), it is safe to hand to a
`BackgroundTasks` or a thread executor; callers must not invoke it while already inside a running
event loop (the CLI scheduler runs it in an executor for exactly that reason).

Every pipeline entry point (`get_settings`, `build_runtime`, `run_measurement`) is imported by
name into this module so tests can patch it as `gw_geo.measurement.trigger.<name>` and keep the
job hermetic (no live runtime, DB, or network).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.billing.metering import UsageKind, record_usage
from gw_geo.common.config import get_settings
from gw_geo.common.models import VisibilitySnapshot
from gw_geo.common.wiring import build_runtime
from gw_geo.measurement.runner import run_measurement

logger = logging.getLogger(__name__)


def run_measurement_job(
    *,
    tenant_id: str,
    brand_id: str,
    engines: list[str],
    geos: list[str] | None = None,
    personas: list[str | None] | None = None,
    n_samples: int | None = None,
    date: str | None = None,
) -> list[VisibilitySnapshot]:
    """Run one measurement pass for `brand_id` locally, returning the resulting snapshots.

    `geos` / `personas` / `n_samples` / `date` fall back to the settings defaults (or today's UTC
    date, and the unpersonalized `[None]` persona set) when omitted. Builds the real runtime from
    env-driven `Settings` -- so a local-only run persists to a `LocalFileArchive` iff
    `GEO_RAW_ARCHIVE_BACKEND=local` is set (see `common.wiring`); otherwise it uses S3 exactly like
    the Lambda handler. Opens (and always closes) its own `Session`, and runs the async pipeline to
    completion via `asyncio.run`.
    """
    settings = get_settings()
    runtime = build_runtime(settings)

    resolved_geos = geos if geos is not None else list(settings.default_geos)
    resolved_personas: list[str | None] = personas if personas is not None else [None]
    resolved_n = n_samples if n_samples is not None else settings.default_n_samples
    resolved_date = date or datetime.now(timezone.utc).date().isoformat()

    logger.info(
        "measurement job start tenant_id=%s brand_id=%s engines=%s geos=%s n=%d date=%s",
        tenant_id,
        brand_id,
        engines,
        resolved_geos,
        resolved_n,
        resolved_date,
    )

    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        snapshots = asyncio.run(
            run_measurement(
                session=session,
                tenant_id=tenant_id,
                brand_id=brand_id,
                engines=list(engines),
                geos=resolved_geos,
                personas=resolved_personas,
                n_samples=resolved_n,
                extractor=runtime["extractor"],
                archive=runtime["archive"],
                date=resolved_date,
            )
        )
        # Billing metering (m4-design §4.1): probing dominates cost, so record the run's total
        # sampled probes as one PROBE usage event. Skipped when nothing was sampled (no billable
        # work, and keeps a mocked/empty run from touching an un-provisioned DB).
        probe_units = sum(snapshot.n_samples for snapshot in snapshots)
        if probe_units > 0:
            record_usage(
                session,
                tenant_id=tenant_id,
                brand_id=brand_id,
                kind=UsageKind.PROBE,
                quantity=probe_units,
                ts=resolved_date,
            )
            session.commit()
    finally:
        session.close()

    logger.info(
        "measurement job done tenant_id=%s brand_id=%s snapshots=%d",
        tenant_id,
        brand_id,
        len(snapshots),
    )
    return snapshots
