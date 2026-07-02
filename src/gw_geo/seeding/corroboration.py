"""Corroboration tracking: how many independent sources now back the brand (m4-design.md S2.6).

After a seeding placement lands, `corroboration_count` measures **consensus**: models weight a
brand fact more when several independent sources agree on it (PRD S6.5), so a placement's real
payoff is not just "did it get posted" but "did it move the citation-source mix". This module
reads that mix through the same injected `SourceMap` protocol T05 (`seeding/discovery.py`) already
defined -- in production, `measurement/feed.citation_source_mix` (M1); in tests, a hermetic fake --
so this module builds and tests independently of M1, with no live database or network call.

`SourceMap.citation_source_mix` returns `{"sources": [{"domain", "source_type", "engine",
"you_pct", ...}, ...]}`, one row per (domain, engine) pair observed in `[since, until]` (see
`seeding/discovery.py` for the full row shape). A domain **corroborates** the brand when an engine
now cites it there at all (`you_pct > 0`); it counts only if it is **independent** of the brand,
i.e. not the brand's own property (`source_type != "own_site"`) -- a brand's own site restating its
own facts is not third-party consensus. The same domain can appear more than once (once per engine
that cites it there); `corroboration_count` counts each qualifying **domain** once, not once per
engine/row, since it is domains -- not per-engine observations -- that constitute independent
corroboration.

`update_corroboration` is the write path: given a `seeding_task` id, it resolves that task's
`brand_id`, recomputes `corroboration_count` for it, and persists the fresh count onto
`seeding_task.corroboration_count` (m4-design.md S2.7) -- the field the seeding tracker (ui-spec.md
S3.5) and the ranking model's `corroboration_count` feature (`ranking/features.py`) both read.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from gw_geo.common.db import SeedingTask
from gw_geo.seeding.discovery import SourceMap

_OWN_SITE = "own_site"


def corroboration_count(
    source_map: SourceMap, *, tenant_id: str, brand_id: str, since: str, until: str
) -> int:
    """Count distinct independent domains now citing the brand in `[since, until]`.

    A row counts iff `you_pct > 0` (the brand is actually cited there) and `source_type !=
    "own_site"` (the domain is independent of the brand). The same domain can recur across
    engines; it is counted once regardless of how many qualifying rows name it.
    """
    mix = source_map.citation_source_mix(
        tenant_id=tenant_id, brand_id=brand_id, since=since, until=until
    )
    domains = {
        row["domain"]
        for row in mix.get("sources", [])
        if row["you_pct"] > 0 and row["source_type"] != _OWN_SITE
    }
    return len(domains)


def _get_task(session: Session, *, tenant_id: str, task_id: str) -> SeedingTask:
    """Resolve `task_id`'s `SeedingTask`, scoped to `tenant_id`.

    Raises:
        LookupError: no such task, or it belongs to a different tenant -- the two cases are
            deliberately indistinguishable (matches the `LookupError` "not found" convention used
            for cross-tenant lookups elsewhere, e.g. `measurement/runner.py`,
            `api/routers/brands.py`), so a caller can never use this to probe for another
            tenant's task id.
    """
    task = session.get(SeedingTask, task_id)
    if task is None or task.tenant_id != tenant_id:
        raise LookupError(f"seeding task {task_id!r} not found")
    return task


def update_corroboration(
    session: Session,
    source_map: SourceMap,
    *,
    tenant_id: str,
    task_id: str,
    since: str,
    until: str,
) -> int:
    """Recompute and persist `corroboration_count` for `task_id`'s brand; return the fresh count.

    Looks up `task_id` (tenant-scoped -- see `_get_task`) to resolve its `brand_id`, recomputes
    `corroboration_count` for that brand over `[since, until]`, writes the result onto the task's
    `corroboration_count` column, commits, and returns it. Commits before returning so the count
    is durable for a subsequent read (e.g. the seeding tracker or a fresh session), mirroring
    `measurement/feed.build_rollup`'s same commit-before-return contract for a recomputed,
    persisted derived value.
    """
    task = _get_task(session, tenant_id=tenant_id, task_id=task_id)
    count = corroboration_count(
        source_map, tenant_id=tenant_id, brand_id=task.brand_id, since=since, until=until
    )
    task.corroboration_count = count
    session.commit()
    return count
