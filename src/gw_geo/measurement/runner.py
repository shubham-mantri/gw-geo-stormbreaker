"""End-to-end measurement runner (TRD §5.5) — the pipeline linchpin.

Wires everything M0 built into one orchestrated pass:

    load prompts -> for each (prompt, engine, geo, persona) probe `n_samples`x (async, bounded)
    -> archive raw payload -> parse -> aggregate per (engine, geo, persona) -> persist snapshots
    + citations, all under the per-tenant cost governor (§7).

Two model families meet here (see CLAUDE/task notes): `parse()` and `aggregate()` speak the
**Pydantic** `gw_geo.common.models`, while persistence uses the **ORM** `gw_geo.common.db`.
They are imported under distinct names (`models` vs `db`) and translated explicitly.

Concurrency vs DB safety: probe calls for one (engine, geo, persona) group run concurrently
under an `asyncio.Semaphore`, but the sync SQLAlchemy `Session` is not safe for concurrent use,
so every DB write happens serially on the single session *after* a group's probes have been
gathered. The session is never shared across concurrent tasks.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy.orm import Session

from gw_geo.common import db, models
from gw_geo.common.budget import BudgetExceeded, CostGovernor
from gw_geo.common.models import VisibilitySnapshot
from gw_geo.measurement.aggregate import aggregate
from gw_geo.measurement.parse import Extractor, classify_source, domain_of, parse
from gw_geo.measurement.probe import base
from gw_geo.measurement.probe.base import EngineAdapter

logger = logging.getLogger(__name__)

# Conservative per-probe cost estimate used as the pre-flight budget check (TRD §7). Probing is
# the dominant cost, so the governor gates on this estimate *before* spending; the actual cost is
# recorded on each `ProbeRun` afterwards, which is what the governor sums for subsequent checks.
ESTIMATED_PROBE_COST_USD = 0.02


class RawArchive(Protocol):
    """Sink for raw provider payloads (S3 in deploy, an in-memory store in tests)."""

    def put(self, key: str, payload: dict[str, Any]) -> str:
        """Store `payload` under `key`; return the storage key/ref to record on the ProbeRun."""
        ...


def _new_id() -> str:
    return uuid.uuid4().hex


async def _probe_one(
    adapter: EngineAdapter,
    semaphore: asyncio.Semaphore,
    prompt_text: str,
    *,
    geo: str,
    persona: str | None,
) -> models.ProbeResult:
    """Run a single probe under the concurrency semaphore."""
    async with semaphore:
        return await adapter.probe(prompt_text, geo=geo, persona=persona)


def _persist_probe(
    session: Session,
    *,
    tenant_id: str,
    prompt_id: str,
    engine: str,
    geo: str,
    persona: str | None,
    result: models.ProbeResult,
    raw_ref: str,
) -> str:
    """Insert a successful `db.ProbeRun`; return its generated id."""
    probe_run_id = _new_id()
    session.add(
        db.ProbeRun(
            id=probe_run_id,
            tenant_id=tenant_id,
            prompt_id=prompt_id,
            engine=engine,
            geo=geo,
            persona=persona,
            status="ok",
            raw_answer_s3_key=raw_ref,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )
    )
    return probe_run_id


def _persist_extraction(
    session: Session,
    *,
    tenant_id: str,
    extraction: models.AnswerExtraction,
) -> None:
    """Insert a `db.AnswerExtraction` from the Pydantic `extraction`.

    The ORM row has no `source_types` column (Pydantic-only), so the full Pydantic dump is
    stashed in `raw_json` to preserve it; the `sentiment` enum is mapped to its string value.
    """
    session.add(
        db.AnswerExtraction(
            id=_new_id(),
            tenant_id=tenant_id,
            probe_run_id=extraction.probe_run_id,
            brand_mentioned=extraction.brand_mentioned,
            position=extraction.position,
            sentiment=extraction.sentiment.value,
            cited_urls=list(extraction.cited_urls),
            competitors_present=list(extraction.competitors_present),
            raw_json=extraction.model_dump(mode="json"),
        )
    )


def _upsert_citations(
    session: Session,
    *,
    tenant_id: str,
    brand_id: str,
    engine: str,
    prompt_id: str,
    cited_urls: list[str],
) -> None:
    """Upsert one `db.Citation` per cited URL, keyed on (tenant_id, brand_id, url).

    Bumps `seen_count` + `last_seen` for a known URL, else inserts a fresh row (`seen_count=1`).
    Autoflush makes a row inserted earlier in the same run visible to a later lookup here.
    """
    for url in cited_urls:
        existing = (
            session.query(db.Citation)
            .filter(
                db.Citation.tenant_id == tenant_id,
                db.Citation.brand_id == brand_id,
                db.Citation.url == url,
            )
            .one_or_none()
        )
        if existing is not None:
            existing.seen_count += 1
            existing.last_seen = datetime.now(timezone.utc)
        else:
            session.add(
                db.Citation(
                    id=_new_id(),
                    tenant_id=tenant_id,
                    brand_id=brand_id,
                    url=url,
                    domain=domain_of(url),
                    source_type=classify_source(url).value,
                    engine=engine,
                    prompt_id=prompt_id,
                )
            )


def _persist_snapshot(
    session: Session, *, tenant_id: str, snapshot: VisibilitySnapshot
) -> None:
    """Insert a `db.VisibilitySnapshot` from the Pydantic `snapshot` (adds id + tenant_id)."""
    session.add(
        db.VisibilitySnapshot(
            id=_new_id(),
            tenant_id=tenant_id,
            brand_id=snapshot.brand_id,
            engine=snapshot.engine,
            geo=snapshot.geo,
            persona=snapshot.persona,
            date=snapshot.date,
            mention_rate=snapshot.mention_rate,
            citation_rate=snapshot.citation_rate,
            avg_position=snapshot.avg_position,
            sentiment_score=snapshot.sentiment_score,
            share_of_voice=snapshot.share_of_voice,
            n_samples=snapshot.n_samples,
            ci_low=snapshot.ci_low,
            ci_high=snapshot.ci_high,
        )
    )


async def run_measurement(
    *,
    session: Session,
    tenant_id: str,
    brand_id: str,
    engines: list[str],
    geos: list[str],
    personas: list[str | None],
    n_samples: int,
    extractor: Extractor,
    archive: RawArchive,
    date: str,
    max_concurrency: int = 8,
) -> list[VisibilitySnapshot]:
    """Probe → parse → aggregate → persist for one brand, returning the Pydantic snapshots.

    One `VisibilitySnapshot` is produced per (engine, geo, persona) group that yielded at least
    one probe; its `n_samples` is `n_prompts * n_samples` for that group. Engines the tenant
    cannot afford are skipped (flagged) and the run returns a partial/empty result rather than
    raising `BudgetExceeded` (graceful degradation, TRD §7).
    """
    brand_row = session.get(db.Brand, brand_id)
    if brand_row is None or brand_row.tenant_id != tenant_id:
        logger.warning("brand_id=%r not found for tenant_id=%r; nothing to measure", brand_id, tenant_id)
        return []

    brand = models.Brand(
        id=brand_row.id,
        tenant_id=brand_row.tenant_id,
        name=brand_row.name,
        domain=brand_row.domain,
        competitors=list(brand_row.competitors),
    )

    prompts = (
        session.query(db.Prompt)
        .filter(db.Prompt.tenant_id == tenant_id, db.Prompt.brand_id == brand_id)
        .order_by(db.Prompt.id)
        .all()
    )
    if not prompts:
        logger.warning("no prompts for brand_id=%r tenant_id=%r; nothing to measure", brand_id, tenant_id)
        return []

    governor = CostGovernor(session, tenant_id)
    semaphore = asyncio.Semaphore(max_concurrency)
    snapshots: list[VisibilitySnapshot] = []

    for engine in engines:
        try:
            adapter = base.get_adapter(engine)
        except KeyError:
            logger.warning("no adapter registered for engine=%r; skipping", engine)
            continue

        engine_over_budget = False
        for geo in geos:
            if engine_over_budget:
                break
            for persona in personas:
                if engine_over_budget:
                    break

                # Pre-flight budget guard (per-probe estimate). Accumulated actual spend from
                # persisted ProbeRuns of earlier groups is visible here via the governor.
                try:
                    governor.check(ESTIMATED_PROBE_COST_USD)
                except BudgetExceeded:
                    logger.warning(
                        "budget exceeded for tenant_id=%r engine=%r; skipping engine (flagged)",
                        tenant_id,
                        engine,
                    )
                    engine_over_budget = True
                    break

                # Gather this group's probes concurrently, then persist serially.
                group_prompts = [p for p in prompts for _ in range(n_samples)]
                coros = [
                    _probe_one(adapter, semaphore, p.text, geo=geo, persona=persona)
                    for p in group_prompts
                ]
                results = await asyncio.gather(*coros, return_exceptions=True)

                group_extractions: list[models.AnswerExtraction] = []
                for prompt_row, result in zip(group_prompts, results, strict=True):
                    if isinstance(result, BaseException):
                        logger.warning(
                            "probe failed engine=%r prompt_id=%r: %r", engine, prompt_row.id, result
                        )
                        session.add(
                            db.ProbeRun(
                                id=_new_id(),
                                tenant_id=tenant_id,
                                prompt_id=prompt_row.id,
                                engine=engine,
                                geo=geo,
                                persona=persona,
                                status="error",
                                cost_usd=0.0,
                                latency_ms=0,
                            )
                        )
                        continue

                    raw_ref = archive.put(
                        f"probe/{tenant_id}/{brand_id}/{engine}/{_new_id()}.json", result.raw
                    )
                    probe_run_id = _persist_probe(
                        session,
                        tenant_id=tenant_id,
                        prompt_id=prompt_row.id,
                        engine=engine,
                        geo=geo,
                        persona=persona,
                        result=result,
                        raw_ref=raw_ref,
                    )
                    extraction = parse(result, brand, extractor, probe_run_id)
                    group_extractions.append(extraction)
                    _persist_extraction(session, tenant_id=tenant_id, extraction=extraction)
                    _upsert_citations(
                        session,
                        tenant_id=tenant_id,
                        brand_id=brand_id,
                        engine=engine,
                        prompt_id=prompt_row.id,
                        cited_urls=extraction.cited_urls,
                    )

                if not group_extractions:
                    session.commit()
                    continue

                snapshot = aggregate(
                    group_extractions,
                    brand_id=brand_id,
                    engine=engine,
                    geo=geo,
                    persona=persona,
                    date=date,
                )
                _persist_snapshot(session, tenant_id=tenant_id, snapshot=snapshot)
                session.commit()
                snapshots.append(snapshot)

    return snapshots
