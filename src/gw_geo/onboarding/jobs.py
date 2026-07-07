"""In-memory job store + runner for the **async** domain-first onboarding suggestion (M5).

The grounded competitor pipeline (:func:`gw_geo.onboarding.suggest.suggest_brand_details`) is sync
and takes ~1-2 min (three Opus calls, one with a live web search), which is far longer than a dev
proxy will hold an HTTP connection. So ``POST /brands/suggest`` no longer runs it inline: it seeds a
job here, kicks the pipeline onto a background **thread** (the pipeline is sync+blocking, so it must
run off the event loop or concurrent status polls would block), and returns a ``job_id`` (202). The
client then polls ``GET /brands/suggest/status/{job_id}`` until ``done`` (carrying the
:class:`~gw_geo.onboarding.suggest.BrandSuggestion`) or ``error``. The pipeline's ``on_progress``
hook streams each stage into the job so the poll surfaces live progress.

LOCAL-ONLY, single-user: the store is a process-local dict guarded by a ``threading.Lock`` (the
pipeline runs on a worker thread while poll requests hit the event-loop thread, so concurrent access
is real). It is deliberately **not** durable -- a job lost on reload just makes the client restart
the lookup, which is cheap and safe. No DB, no cloud.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, replace
from typing import Literal

from gw_geo.content.generate import LLMClient
from gw_geo.onboarding.suggest import BrandSuggestion, suggest_brand_details
from gw_geo.ranking.fetch import PageFetcher

logger = logging.getLogger(__name__)

JobStatus = Literal["running", "done", "error"]


@dataclass(frozen=True)
class SuggestJob:
    """One suggestion job's observable state -- exactly what ``GET .../status/{id}`` returns.

    ``result`` is populated only on ``status == "done"``; ``error`` only on ``status == "error"``.
    ``stage``/``label`` are the current pipeline stage (see
    :data:`gw_geo.onboarding.suggest._STAGE_LABELS`) so the client can render live progress.
    """

    status: JobStatus
    stage: str
    label: str
    result: BrandSuggestion | None = None
    error: str | None = None


class SuggestJobStore:
    """A thread-safe, in-memory ``job_id -> SuggestJob`` map (see the module docstring).

    Every mutation is guarded by a single lock. Terminal jobs are immutable: :meth:`set_stage` is a
    no-op once a job is ``done``/``error`` so a late progress update can never resurrect it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, SuggestJob] = {}

    def create(self) -> str:
        """Seed a fresh ``running`` job and return its ``job_id`` (a ``uuid4().hex``)."""
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = SuggestJob(
                status="running", stage="starting", label="Starting…"
            )
        return job_id

    def get(self, job_id: str) -> SuggestJob | None:
        """The current state of ``job_id``, or ``None`` if unknown (-> the endpoint 404s)."""
        with self._lock:
            return self._jobs.get(job_id)

    def set_stage(self, job_id: str, stage: str, label: str) -> None:
        """Advance a *running* job's ``stage``/``label`` (no-op if unknown or already terminal)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running":
                return
            self._jobs[job_id] = replace(job, stage=stage, label=label)

    def finish(self, job_id: str, result: BrandSuggestion) -> None:
        """Mark ``job_id`` ``done`` with its ``result`` (no-op if the job is unknown)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._jobs[job_id] = replace(
                job, status="done", stage="done", label="Done", result=result
            )

    def fail(self, job_id: str, error: str) -> None:
        """Mark ``job_id`` ``error`` with a message (no-op if the job is unknown)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._jobs[job_id] = replace(
                job, status="error", error=error or "suggestion failed"
            )


def run_suggest_job(
    store: SuggestJobStore,
    job_id: str,
    *,
    domain: str,
    fetcher: PageFetcher,
    llm: LLMClient,
    critic: LLMClient,
) -> None:
    """Run the (sync, blocking) suggest pipeline for ``job_id``, streaming progress into ``store``.

    Meant to run on a background thread (off the event loop). Each pipeline stage advances the job
    via ``on_progress``; the final :class:`~gw_geo.onboarding.suggest.BrandSuggestion` is stored via
    :meth:`SuggestJobStore.finish`. The pipeline is already total (never raises), but a belt-and-
    braces ``except`` records ``status="error"`` and logs, so nothing can escape the worker thread.
    """

    def on_progress(stage: str, label: str) -> None:
        store.set_stage(job_id, stage, label)

    try:
        result = suggest_brand_details(
            domain=domain, fetcher=fetcher, llm=llm, critic=critic, on_progress=on_progress
        )
    except Exception as exc:  # never let a background-thread exception vanish silently
        logger.exception("async suggest job %s failed", job_id)
        store.fail(job_id, str(exc))
    else:
        store.finish(job_id, result)


__all__ = ["JobStatus", "SuggestJob", "SuggestJobStore", "run_suggest_job"]
