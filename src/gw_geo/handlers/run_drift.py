"""AWS Lambda handler for the M1 daily drift canary (TRD §5.6, m1-design §4, T17).

Wires real dependencies via `gw_geo.common.wiring.build_runtime` (the same runtime `cli.py` and
`handlers/run_measurement.py` use) and drives `gw_geo.orchestration.drift.run_drift_canary`, so
the deployed Lambda (the `run_drift` function in `serverless.yml`, fired daily by an EventBridge
cron) exercises the identical pipeline as any other caller of the canary.

`build_runtime`'s returned shape is still owned by T18 (`docs/tasks/M1-T18-build-runtime-wiring.md`,
Wave 3 sibling of this task): this handler does not assume it contains an `"alert"` key. Instead
it builds its own SNS-backed alert hook here, from `settings.drift_sns_topic_arn`, via
`make_sns_alert_hook`.

`build_runtime` and `run_drift_canary` are imported by name (not via their owning modules) so
tests can patch `gw_geo.handlers.run_drift.build_runtime` / `.run_drift_canary` directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

# boto3 ships no py.typed marker / stubs, so mypy can't analyze it (see `common/wiring.py`'s
# docstring) -- the SNS client is likewise constructed lazily, never at import time.
import boto3  # type: ignore[import-untyped]
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import get_settings
from gw_geo.common.wiring import build_runtime
from gw_geo.orchestration.drift import AlertHook, DriftResult, run_drift_canary

logger = logging.getLogger(__name__)


def make_sns_alert_hook(topic_arn: str, *, sns_client: Any | None = None) -> AlertHook:
    """Return an `AlertHook` (TRD §5.6) that publishes a structured drift breach to SNS.

    A missing/empty `topic_arn` (e.g. a deploy that hasn't provisioned the alert topic yet, or a
    purely local run) degrades gracefully to a structured log line -- a misconfigured alert
    destination must never crash the canary run itself. Likewise, a publish failure (bad ARN,
    throttling, ...) is logged rather than raised, so one alert failing to send never stops the
    remaining canaries in the same `run_drift_canary` pass from running.

    `sns_client` is injectable for tests (`moto`); when omitted, a real boto3 client is
    constructed lazily on first use, mirroring `wiring.S3RawArchive`.
    """
    client = sns_client

    def alert(result: DriftResult) -> None:
        if not topic_arn:
            logger.warning(
                "drift breach (no SNS topic configured): engine=%s canary_id=%s "
                "baseline_rate=%.3f observed_rate=%.3f drop=%.3f",
                result.engine,
                result.canary_id,
                result.baseline_rate,
                result.observed_rate,
                result.drop,
            )
            return

        nonlocal client
        if client is None:
            client = boto3.client("sns")

        message = {
            "engine": result.engine,
            "canary_id": result.canary_id,
            "baseline_rate": result.baseline_rate,
            "observed_rate": result.observed_rate,
            "drop": result.drop,
            "breached": result.breached,
        }
        try:
            client.publish(
                TopicArn=topic_arn,
                Subject=f"GEO drift breach: {result.engine}/{result.canary_id}",
                Message=json.dumps(message),
            )
        except Exception:
            logger.exception(
                "drift alert publish failed: engine=%s canary_id=%s",
                result.engine,
                result.canary_id,
            )

    return alert


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run one daily drift-canary pass (TRD §5.6).

    `event` (all optional): `engines` (list[str], default: the engines `build_runtime` just
    registered) and `date` (`YYYY-MM-DD`, default: today, UTC). `context` is the Lambda context
    object, unused here.

    Returns `{"results": [DriftResult...], "breaches": <int>}`, each result a JSON-safe dict.
    """
    settings = get_settings()
    runtime = build_runtime(settings)
    alert = make_sns_alert_hook(settings.drift_sns_topic_arn)

    engines: list[str] = list(event.get("engines") or runtime["engines"])
    date: str = event.get("date") or datetime.now(timezone.utc).date().isoformat()

    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        results = asyncio.run(
            run_drift_canary(
                session=session,
                engines=engines,
                threshold=settings.drift_threshold,
                extractor=runtime["extractor"],
                archive=runtime["archive"],
                date=date,
                alert_hook=alert,
            )
        )
    finally:
        session.close()

    breaches = sum(1 for result in results if result.breached)
    return {"results": [result.model_dump() for result in results], "breaches": breaches}
