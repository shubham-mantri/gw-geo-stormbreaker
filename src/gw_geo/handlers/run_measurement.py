"""AWS Lambda handler for the M0 measurement pipeline (TRD §11).

Wires the same real dependencies as `gw_geo.cli` (via `gw_geo.common.wiring.build_runtime`) and
drives the same `gw_geo.measurement.runner.run_measurement` pipeline, so the CLI and the deployed
Lambda (see the `runMeasurement` function in `serverless.yml`) never diverge in behavior.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import get_settings
from gw_geo.common.wiring import build_runtime
from gw_geo.measurement.runner import run_measurement


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run one measurement pass for the brand/engines/geos described by `event`.

    `event` keys: `tenant_id`, `brand_id`, `engines` (list[str]), `geos` (list[str]), `n_samples`
    (int); optionally `personas` (list[str | None], default `[None]`) and `date` (`YYYY-MM-DD`,
    default: today, UTC). `context` is the Lambda context object, unused here.

    Returns `{"snapshots": [...]}`, each entry a `VisibilitySnapshot` dumped as a JSON-safe dict.
    """
    settings = get_settings()
    runtime = build_runtime(settings)

    personas: list[str | None] = list(event.get("personas") or [None])
    date: str = event.get("date") or datetime.now(timezone.utc).date().isoformat()

    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        snapshots = asyncio.run(
            run_measurement(
                session=session,
                tenant_id=event["tenant_id"],
                brand_id=event["brand_id"],
                engines=list(event["engines"]),
                geos=list(event["geos"]),
                personas=personas,
                n_samples=int(event["n_samples"]),
                extractor=runtime["extractor"],
                archive=runtime["archive"],
                date=date,
            )
        )
    finally:
        session.close()

    return {"snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots]}
