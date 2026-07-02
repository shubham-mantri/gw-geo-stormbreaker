"""Ranking labels (M3-T05, TRD §8): cited-vs-not per (tenant, brand, engine) from measurement.

A URL is cited (1) for `(brand, engine)` iff a matching `Citation` row -- M0's measurement system
of record, written by `measurement/runner.py`'s `_upsert_citations` -- exists for that
tenant/brand/engine; `dataset.build_dataset` turns membership in this set into the `0`/`1` label.
`Citation.url` is already normalized at write time (`measurement/parse.normalize_url`, applied
before the `Citation` insert -- see `runner.py`'s `_upsert_citations` docstring), so this read
returns the column verbatim; no re-normalization happens here.

Every query filters `tenant_id` (and `brand_id`, `engine`) explicitly, matching the explicit-
filter convention used by `measurement/feed.py` / `measurement/runner.py` / `common/budget.py`
(TRD §7: tenant-scoped reads) rather than routing through `TenantScopedSession` (which only wraps
single-`tenant_id` lookups like `Brand`, not this three-way tenant/brand/engine scope).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from gw_geo.common.db import Citation


def cited_urls_for(session: Session, *, tenant_id: str, brand_id: str, engine: str) -> set[str]:
    """Return the set of `Citation.url` values recorded for `(tenant_id, brand_id, engine)`.

    This is the measurement-derived label source for the per-engine ranking models (TRD §8): a
    candidate URL is "cited" for the engine iff it is a member of this set.
    """
    stmt = select(Citation.url).where(
        Citation.tenant_id == tenant_id,
        Citation.brand_id == brand_id,
        Citation.engine == engine,
    )
    return set(session.execute(stmt).scalars())
