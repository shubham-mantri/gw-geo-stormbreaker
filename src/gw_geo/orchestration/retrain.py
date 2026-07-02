"""Retrain trigger: drift-breach -> ranking-model retrain (m4-design §3.1, PRD §6.6).

Extends the M1 drift canary (`orchestration/drift.py`) into self-adaptation (PRD §6.6: "when
citation rates for known-good patterns drop sharply -> alert + trigger re-measurement +
retrain"): a breached `drift_event` with `retrain_flag=True` triggers a **retrain job** for the
affected engine's ranking model. The trainer is an **injected `Retrainer` protocol** (satisfied
by the M3 `ranking/` trainer) so this module -- and every test in this suite -- never trains a
real model or pulls live data; CI only ever exercises the trigger/bookkeeping logic.

Idempotency is enforced at the application layer (check-then-write, mirroring
`attribution/ingest.py`'s style -- there is no DB-level uniqueness constraint on
`retrain_job.trigger_drift_event_id`): `on_breach` looks up any existing `retrain_job` for the
given `drift_event_id` before creating one, so calling it twice for the same event -- whether
from a re-run `poll()` or a duplicate alert delivery -- never spawns a second job or calls the
injected `retrainer` more than once. This holds even if the existing job previously failed: a
failed retrain is a signal for an operator to investigate, not something this module silently
retries on its own (see `on_breach`/`poll` docstrings).

`retrain_job`/`drift_event` are both **system-level** (no `tenant_id`): engine drift, and the
retraining it triggers, are properties of the engine, not of any one tenant (same documented
exception as `DriftEvent`, m1-design §6) -- so `RetrainTrigger` takes a plain SQLAlchemy
`Session`, not a `TenantScopedSession`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy.orm import Session

from gw_geo.common import db

logger = logging.getLogger(__name__)

_JobStatus = Literal["pending", "running", "succeeded", "failed"]
_JOB_STATUSES: tuple[_JobStatus, ...] = ("pending", "running", "succeeded", "failed")


class RetrainJob(BaseModel):
    """Read model for one triggered retrain run (m4-design §3.1)."""

    id: str
    model_engine: str
    trigger_drift_event_id: str
    status: Literal["pending", "running", "succeeded", "failed"]
    metrics_before: dict[str, float]
    metrics_after: dict[str, float]
    model_ref: str | None = None


class Retrainer(Protocol):
    """Trains (or re-trains) an engine's ranking model; satisfied by the M3 ranking trainer.

    Returns `{"model_ref": <str>, "metrics": {...}}` describing the freshly trained model.
    Real training/data access lives entirely behind this seam -- tests inject a fake, so no
    live call is ever made from this module or its test suite.
    """

    def retrain(self, *, engine: str) -> dict[str, Any]: ...


def _to_pydantic(row: db.RetrainJob) -> RetrainJob:
    """Convert a persisted `retrain_job` row into its `RetrainJob` read model.

    `row.status` is a plain `str` column (no DB-level enum). Checked against `_JOB_STATUSES`
    before use, so a corrupted/out-of-band status value fails loudly here instead of silently
    reaching a caller -- the check also narrows the type for `mypy`, which otherwise sees `str`
    where the model's field is a `Literal`.
    """
    if row.status not in _JOB_STATUSES:
        raise ValueError(f"retrain_job {row.id!r} has unexpected status {row.status!r}")
    return RetrainJob(
        id=row.id,
        model_engine=row.model_engine,
        trigger_drift_event_id=row.trigger_drift_event_id,
        status=row.status,
        metrics_before=row.metrics_before,
        metrics_after=row.metrics_after,
        model_ref=row.model_ref,
    )


class RetrainTrigger:
    """Turns breached+flagged `drift_event` rows into retrain jobs via an injected `Retrainer`."""

    def __init__(self, session: Session, *, retrainer: Retrainer) -> None:
        self._session = session
        self._retrainer = retrainer

    def _existing_job(self, drift_event_id: str) -> db.RetrainJob | None:
        return (
            self._session.query(db.RetrainJob)
            .filter(db.RetrainJob.trigger_drift_event_id == drift_event_id)
            .first()
        )

    def on_breach(self, drift_event_id: str) -> RetrainJob:
        """Idempotently trigger (or return) the one retrain job for `drift_event_id`.

        A pre-existing `retrain_job` for this event -- from an earlier call, whether it
        succeeded or failed -- is returned as-is and `retrainer.retrain` is not called again.
        Otherwise: create the job row (`status="running"`) and commit it *before* calling the
        retrainer, so the attempt is durable even if `retrainer.retrain` raises or the process
        dies mid-call. On success, the job is marked `"succeeded"` with `model_ref`/
        `metrics_after` populated from the retrainer's result, and the triggering event's
        `retrain_flag` is cleared. On any exception from `retrainer.retrain`, the job is marked
        `"failed"` and the event's `retrain_flag` is left set -- a failed retrain surfaces as a
        standing signal for an operator, rather than being swallowed or auto-retried.

        Raises `ValueError` if `drift_event_id` names no `drift_event` row.
        """
        existing = self._existing_job(drift_event_id)
        if existing is not None:
            return _to_pydantic(existing)

        event = self._session.get(db.DriftEvent, drift_event_id)
        if event is None:
            raise ValueError(f"no drift_event with id={drift_event_id!r}")

        job = db.RetrainJob(
            id=uuid4().hex,
            model_engine=event.engine,
            trigger_drift_event_id=drift_event_id,
            status="running",
            metrics_before={},
            metrics_after={},
            model_ref=None,
        )
        self._session.add(job)
        self._session.commit()

        try:
            result = self._retrainer.retrain(engine=event.engine)
        except Exception as exc:
            logger.warning(
                "retrain failed engine=%s drift_event_id=%s job_id=%s: %r",
                event.engine,
                drift_event_id,
                job.id,
                exc,
            )
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            self._session.commit()
            return _to_pydantic(job)

        job.status = "succeeded"
        job.model_ref = result.get("model_ref")
        job.metrics_after = dict(result.get("metrics", {}))
        job.completed_at = datetime.now(timezone.utc)
        event.retrain_flag = False
        self._session.commit()
        return _to_pydantic(job)

    def poll(self) -> list[RetrainJob]:
        """Trigger a retrain for every currently unhandled breached+flagged `drift_event`.

        "Unhandled" means no `retrain_job` references the event yet. Once `on_breach` creates a
        job for an event -- success or failure -- that event is not re-selected by a later
        `poll()` even if its `retrain_flag` is still set: `poll` never auto-retries a failed
        retrain (see `on_breach`); doing so is a deliberate, explicit `on_breach` call, not
        something a periodic scan does on its own.
        """
        handled_event_ids = {
            row.trigger_drift_event_id for row in self._session.query(db.RetrainJob).all()
        }
        breaches = (
            self._session.query(db.DriftEvent)
            .filter(db.DriftEvent.breached.is_(True), db.DriftEvent.retrain_flag.is_(True))
            .all()
        )
        return [
            self.on_breach(event.id) for event in breaches if event.id not in handled_event_ids
        ]
