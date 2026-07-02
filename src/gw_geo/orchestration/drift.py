"""Drift canary: detect when an engine's behavior shifts (TRD §5.6, m1-design §4).

Runs a small, fixed set of canaries -- (engine, prompt, brand) triples with a known-good
baseline mention rate -- through the normal registered adapters, reusing the same `parse()` +
`aggregate()` machinery as the measurement runner (`measurement/runner.py`) so drift is judged
by the identical pipeline that produces real visibility snapshots. When an engine's observed
rate has dropped more than `threshold` below its baseline, this writes a system-level
`DriftEvent` (`retrain_flag=True`) and calls an injected alert hook (a structured log locally;
`handlers/run_drift.py` (T17) wires a real SNS-backed hook at deploy).

`DriftEvent` intentionally has no `tenant_id` (m1-design §6): engine drift is a property of the
engine/canary, not of any one tenant, so the canary set and its baselines are configured once,
system-wide.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy.orm import Session

from gw_geo.common import db
from gw_geo.common.models import AnswerExtraction, Brand
from gw_geo.measurement.aggregate import aggregate
from gw_geo.measurement.parse import Extractor, parse
from gw_geo.measurement.probe import base
from gw_geo.measurement.runner import RawArchive

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.2

# How many times each canary is probed to estimate its current rate. Small and fixed for now --
# canaries are a cheap, high-signal smoke test, not a full sampling run (TRD §5.6).
_SAMPLES_PER_CANARY = 5

# Canaries are system-level (no tenant); this id only satisfies the throwaway `Brand` passed to
# `parse()` below and is never persisted.
_SYSTEM_TENANT_ID = "system"


class DriftResult(BaseModel):
    engine: str
    canary_id: str
    baseline_rate: float
    observed_rate: float
    drop: float
    breached: bool


class Canary(BaseModel):
    canary_id: str
    engine: str
    prompt: str
    brand: str
    baseline_rate: float  # known-good mention/citation rate


# Fixed canary seed set (m1-design §4: "stored via config/seed"). A handful of unambiguous
# prompts per engine whose answers reliably name the seed brand today. Curating/expanding this
# set is an ops task, not a code change -- `load_canaries` is the seam a future DB-backed source
# would replace.
_CANARY_SEED: list[Canary] = [
    Canary(
        canary_id="perplexity-crm-baseline",
        engine="perplexity",
        prompt="What is Salesforce?",
        brand="Salesforce",
        baseline_rate=0.9,
    ),
    Canary(
        canary_id="openai-crm-baseline",
        engine="openai",
        prompt="What is Salesforce?",
        brand="Salesforce",
        baseline_rate=0.9,
    ),
    Canary(
        canary_id="gemini-crm-baseline",
        engine="gemini",
        prompt="What is Salesforce?",
        brand="Salesforce",
        baseline_rate=0.9,
    ),
]


def load_canaries(session: Session | None = None) -> list[Canary]:
    """Return the fixed canary set.

    `session` is accepted (and unused) so a future DB-backed canary source can drop in behind
    this same call site without changing any caller; for now the set is the `_CANARY_SEED`
    config/seed constant (m1-design §4).
    """
    return list(_CANARY_SEED)


AlertHook = Callable[[DriftResult], None]


def _default_alert_hook(result: DriftResult) -> None:
    """Structured-log alert. `handlers/run_drift.py` (T17) wires a real SNS-backed hook at deploy."""
    logger.warning(
        "drift breach: engine=%s canary_id=%s baseline_rate=%.3f observed_rate=%.3f drop=%.3f",
        result.engine,
        result.canary_id,
        result.baseline_rate,
        result.observed_rate,
        result.drop,
    )


def _new_id() -> str:
    return uuid.uuid4().hex


async def _observed_rate(
    canary: Canary, *, extractor: Extractor, archive: RawArchive, date: str
) -> float:
    """Probe `canary`'s adapter `_SAMPLES_PER_CANARY`x and return the aggregated mention rate.

    Each probe is isolated exactly like `runner.py`'s per-probe resilience: probes are gathered
    with `return_exceptions=True`, and each successful result's archive+parse is wrapped, so one
    failed probe (HTTP error, bad payload, or extractor raise) is logged and dropped instead of
    aborting the whole canary. Only the successful `ProbeResult`s are aggregated. If *every* probe
    for this canary failed -- typically a full engine outage, the strongest possible drift signal
    -- the rate is `0.0` (so `drop = baseline_rate - 0.0`, normally a breach) rather than a raised
    exception that would discard every other canary's already-computed result.
    """
    adapter = base.get_adapter(canary.engine)
    brand = Brand(id=canary.canary_id, tenant_id=_SYSTEM_TENANT_ID, name=canary.brand, domain="")

    probe_results = await asyncio.gather(
        *(adapter.probe(canary.prompt) for _ in range(_SAMPLES_PER_CANARY)),
        return_exceptions=True,
    )

    extractions: list[AnswerExtraction] = []
    for probe_result in probe_results:
        if isinstance(probe_result, BaseException):
            logger.warning(
                "drift probe failed engine=%s canary_id=%s: %r",
                canary.engine,
                canary.canary_id,
                probe_result,
            )
            continue
        try:
            archive.put(
                f"drift/{canary.engine}/{canary.canary_id}/{_new_id()}.json", probe_result.raw
            )
            extractions.append(parse(probe_result, brand, extractor, _new_id()))
        except Exception as exc:
            logger.warning(
                "drift archive/parse failed engine=%s canary_id=%s: %r",
                canary.engine,
                canary.canary_id,
                exc,
            )

    if not extractions:
        logger.warning(
            "drift canary produced no usable probes engine=%s canary_id=%s; "
            "treating observed_rate as 0.0",
            canary.engine,
            canary.canary_id,
        )
        return 0.0

    snapshot = aggregate(
        extractions,
        brand_id=canary.canary_id,
        engine=canary.engine,
        geo="us",
        persona=None,
        date=date,
    )
    return snapshot.mention_rate


async def run_drift_canary(
    session: Session,
    *,
    engines: list[str],
    threshold: float = DEFAULT_THRESHOLD,
    extractor: Extractor,
    archive: RawArchive,
    date: str,
    alert_hook: AlertHook | None = None,
) -> list[DriftResult]:
    """Probe the fixed canary set, compare observed vs. baseline rates, and flag breaches.

    For each canary whose `engine` is in `engines`: probe the registered adapter enough times to
    estimate a current rate, parse+aggregate it exactly like the measurement runner, and compute
    `drop = baseline_rate - observed_rate`. `breached = drop > threshold`. A breach writes a
    system-level `DriftEvent` row (`retrain_flag=True`, committed immediately) and calls
    `alert_hook` (default: a structured log entry). Returns one `DriftResult` per matched canary,
    in `load_canaries()` order, whether or not it breached.
    """
    hook = alert_hook if alert_hook is not None else _default_alert_hook
    canaries = [c for c in load_canaries(session) if c.engine in engines]

    results: list[DriftResult] = []
    for canary in canaries:
        observed_rate = await _observed_rate(
            canary, extractor=extractor, archive=archive, date=date
        )
        drop = canary.baseline_rate - observed_rate
        breached = drop > threshold

        result = DriftResult(
            engine=canary.engine,
            canary_id=canary.canary_id,
            baseline_rate=canary.baseline_rate,
            observed_rate=observed_rate,
            drop=drop,
            breached=breached,
        )
        results.append(result)

        if breached:
            session.add(
                db.DriftEvent(
                    id=_new_id(),
                    engine=canary.engine,
                    canary_id=canary.canary_id,
                    baseline_rate=canary.baseline_rate,
                    observed_rate=observed_rate,
                    drop=drop,
                    breached=True,
                    retrain_flag=True,
                    ts=datetime.now(timezone.utc),
                )
            )
            session.commit()
            hook(result)

    return results
