"""Ranking-generation orchestrator (M5): source candidates from the DB, then run the ranker.

This is the stateful producer that finally lets the M3 ranking pipeline run from live data instead
of an operator-supplied JSON file. It composes the M5 candidate-sourcing crawler
(`ranking.sourcing.build_ranking_inputs_from_db`) with the pure per-engine ranker
(`ranking.runner.run_ranking`): sourcing turns the `Citation` pool into `{candidates, current,
source_mix}` inputs, and `run_ranking` trains + persists one `FeatureModel` per engine and returns
the per-engine `RankingReport`s. Directly mirrors how `orchestration.opportunity_gen` wraps the pure
`build_opportunities`.

Hermetic core, real edges (TRD §12): `generate_ranking_reports` takes every I/O dependency
injected -- the `PageFetcher`, the `EmbeddingClient`, and the `backend_factory` -- so unit tests
pass fakes and make no live HTTP / embedding / scikit-learn call. `run_ranking_refresh_job` is the
local, in-process job that wires the *real* runtime (`HttpxPageFetcher`, a config-selected embedder,
`ranking.model.make_backend`) and owns its own session, exactly like
`opportunity_gen.run_opportunity_refresh_job`. `get_settings` / `build_embedder` are imported by
name so tests can patch them on this module and keep the job hermetic.

Offline by default: when no embedding key is configured, the runtime falls back to
`LocalHashEmbedder` -- a keyless, deterministic hash-based embedder -- so the whole chain (crawl ->
features -> train -> report) runs fully offline with no external dependency (PRD NG1, LOCAL-ONLY).

The >=2-engine requirement (see `ranking.sourcing`): negatives are sourced cross-engine (a URL some
*other* engine cited but this one didn't). A brand with citations from a single engine yields
all-positive labels and can't train a boundary; `generate_ranking_reports` logs a warning when it
sees fewer than two engines represented.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gw_geo.common.config import Settings, get_settings
from gw_geo.common.db import Brand, Citation
from gw_geo.common.models import RankingReport
from gw_geo.content.gateway import build_embedder
from gw_geo.ranking.features import EmbeddingClient
from gw_geo.ranking.fetch import HttpxPageFetcher, PageFetcher
from gw_geo.ranking.model import ModelBackend, make_backend
from gw_geo.ranking.runner import run_ranking
from gw_geo.ranking.sourcing import (
    build_ranking_inputs_from_db,
    make_corroboration_fn,
    make_domain_authority_fn,
)

logger = logging.getLogger(__name__)

# Fixed dimensionality for the keyless offline embedder. Large enough that token collisions are
# rare, small enough to stay cheap -- it is a similarity proxy, not a semantic model.
_LOCAL_EMBEDDING_DIM = 256


class LocalHashEmbedder:
    """A keyless, deterministic `EmbeddingClient`: hashes each token into a fixed-dim vector.

    Used as the offline fallback when no embedding key is configured, so the ranking chain runs
    with zero external dependency (PRD NG1, LOCAL-ONLY). Each whitespace token is hashed with
    `sha256` (NOT Python's salted `hash()`, which would differ across processes) into one of
    `dim` buckets with a stable +/-1 sign, giving a signed bag-of-tokens vector. Two texts sharing
    tokens land energy in the same buckets, so their cosine similarity is higher than for disjoint
    text -- a usable, fully-offline stand-in for a real embedding model's `embedding_similarity`.
    """

    def __init__(self, dim: int = _LOCAL_EMBEDDING_DIM) -> None:
        self._dim = dim

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in text.lower().split():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] & 1 == 0 else -1.0
            vector[index] += sign
        return vector


def _embedding_key_configured(settings: Settings) -> bool:
    """True iff a real embedding backend is keyed (Portkey gateway, or a direct OpenAI key).

    Mirrors `content.gateway.build_embedder`'s own routing: it returns a Portkey-backed embedder
    when the gateway is selected and keyed, else the direct OpenAI client -- so "configured" means
    exactly one of those two keys is present. When neither is, the offline `LocalHashEmbedder` is
    used instead.
    """
    if settings.llm_gateway == "portkey" and settings.portkey_api_key:
        return True
    return bool(settings.openai_api_key)


def _build_embedder(settings: Settings) -> EmbeddingClient:
    """The runtime embedder: the config-selected real one when keyed, else the offline local one."""
    if _embedding_key_configured(settings):
        return build_embedder(settings)
    return LocalHashEmbedder()


def build_ranking_runtime(
    settings: Settings,
) -> tuple[PageFetcher, EmbeddingClient, Callable[[], ModelBackend]]:
    """Wire the real ranking runtime from `settings`: `(fetcher, embedder, backend_factory)`.

    The three injected I/O dependencies `generate_ranking_reports` needs: a live `HttpxPageFetcher`,
    a config-selected embedder (offline `LocalHashEmbedder` when keyless), and a `backend_factory`
    that builds the config-selected scikit-learn backend fresh per engine (`make_backend` is
    imported eagerly but only *called* inside `run_ranking`, so simply building the runtime never
    requires scikit-learn to be installed). Shared by both `run_ranking_refresh_job` and
    `opportunity_gen.run_execution_refresh_job`.
    """
    fetcher: PageFetcher = HttpxPageFetcher()
    embedder = _build_embedder(settings)

    def backend_factory() -> ModelBackend:
        return make_backend(settings.ranking_model_type)

    return fetcher, embedder, backend_factory


def _engines_with_citations(session: Session, *, tenant_id: str, brand_id: str) -> int:
    """Count the distinct engines that have cited anything for the brand (for the >=2 check)."""
    stmt = (
        select(Citation.engine)
        .where(Citation.tenant_id == tenant_id, Citation.brand_id == brand_id)
        .distinct()
    )
    return len(set(session.execute(stmt).scalars()))


def generate_ranking_reports(
    *,
    session: Session,
    tenant_id: str,
    brand_id: str,
    engines: list[str],
    fetcher: PageFetcher,
    embedder: EmbeddingClient,
    backend_factory: Callable[[], ModelBackend],
    now: str | None = None,
    id_fn: Callable[[], str] | None = None,
    model_type: str = "gbt",
) -> dict[str, RankingReport]:
    """Source candidates from the citation pool and run the per-engine ranker; return the reports.

    Loads the brand (a missing or cross-tenant brand is a no-op returning `{}`, mirroring
    `opportunity_gen.generate_and_persist_opportunities`), builds the local
    `domain_authority_fn`/`corroboration_fn` proxies + the sourced `run_ranking` inputs
    (`build_ranking_inputs_from_db`), then delegates to `run_ranking` -- which applies the per-engine
    cited/not labels, trains + persists one `FeatureModel` per engine, and composes each engine's
    `RankingReport`. Warns when fewer than two engines have citations (all-positive labels; see the
    module docstring). `now` defaults to today (UTC); `id_fn`/`model_type` pass straight through to
    `run_ranking` (inject a deterministic `id_fn` in tests).
    """
    brand = session.get(Brand, brand_id)
    if brand is None or brand.tenant_id != tenant_id:
        logger.warning(
            "brand_id=%r not found for tenant_id=%r; no ranking reports generated",
            brand_id,
            tenant_id,
        )
        return {}

    if _engines_with_citations(session, tenant_id=tenant_id, brand_id=brand_id) < 2:
        logger.warning(
            "brand_id=%r has citations from fewer than 2 engines; ranking negatives are sourced "
            "cross-engine, so per-engine datasets will be all-positive and cannot learn a boundary "
            "(measure >=2 engines before ranking)",
            brand_id,
        )

    resolved_now = now if now is not None else datetime.now(timezone.utc).date().isoformat()
    inputs = build_ranking_inputs_from_db(
        session,
        tenant_id=tenant_id,
        brand_id=brand_id,
        engines=engines,
        fetcher=fetcher,
        embedder=embedder,
        now=resolved_now,
        domain_authority_fn=make_domain_authority_fn(
            session, tenant_id=tenant_id, brand_id=brand_id
        ),
        corroboration_fn=make_corroboration_fn(session, tenant_id=tenant_id, brand_id=brand_id),
    )

    return run_ranking(
        session=session,
        tenant_id=tenant_id,
        brand_id=brand_id,
        engines=engines,
        candidates_by_engine=inputs["candidates_by_engine"],
        backend_factory=backend_factory,
        current_by_engine=inputs["current_by_engine"],
        source_mix_by_engine=inputs["source_mix_by_engine"],
        id_fn=id_fn,
        model_type=model_type,
    )


def run_ranking_refresh_job(
    *, tenant_id: str, brand_id: str, engines: list[str]
) -> dict[str, RankingReport]:
    """Local, in-process ranking refresh for `brand_id`; opens its own `Session` and wires runtime.

    The single unit both the request path (`POST /brands/{id}/ranking/refresh`) and the `rank-live`
    CLI subcommand call, mirroring `opportunity_gen.run_opportunity_refresh_job`. A plain sync
    function that owns and always closes its own session (built from `settings.database_url`) and
    wires the real runtime via `build_ranking_runtime` -- no AWS/Lambda/cloud anywhere. Returns the
    per-engine `RankingReport`s. `get_settings` is imported by name so tests can patch
    `gw_geo.orchestration.ranking_gen.get_settings` and keep the job hermetic.
    """
    settings = get_settings()
    fetcher, embedder, backend_factory = build_ranking_runtime(settings)

    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        reports = generate_ranking_reports(
            session=session,
            tenant_id=tenant_id,
            brand_id=brand_id,
            engines=engines,
            fetcher=fetcher,
            embedder=embedder,
            backend_factory=backend_factory,
            model_type=settings.ranking_model_type,
        )
    finally:
        session.close()

    logger.info(
        "ranking refresh job done tenant_id=%s brand_id=%s engines=%d",
        tenant_id,
        brand_id,
        len(reports),
    )
    return reports
