"""Attribution mechanism 2 (TRD §6 #2, m2-design §2.3): citation-to-page linkage.

Joins an inbound `session` to the *specific* seeded page an AI engine cited, by matching the
session's `landing_url` to a `citation.url` for the same tenant/brand -- and, where a referring
engine is already known for the session (mechanism 1, `attribution/referral.py`), the same engine
too. A match upgrades the attribution with the citation's `id`/`prompt_id`/`engine`: not just "an
AI probably sent this visitor" but "this specific answer, to this specific prompt, did."

**Independent of mechanism 1.** T07 depends only on T05 (`ingest.py`) -- it runs in the same wave
as, and does not import from, T06 (`referral.py`); see `docs/tasks/M2-README.md`'s DAG. `session.
engine` is nullable until mechanism 1 classifies it (`common/db.py::Session`), so this module
treats a `None` engine as "unknown, don't filter on it" rather than requiring mechanism 1 to have
already run first. When `session.engine` *is* already known, it disambiguates which citation to
credit if more than one page-identity match exists across engines. This is a deliberate reading of
the task spec's `(same tenant/brand[/engine])` -- bracketed, i.e. optional -- as opposed to
m2-design.md §2.3's unbracketed "same brand/engine"; flagged here for the orchestrator/user to
confirm it stays consistent with `referral.py` (T06) once that lands.

**Page-identity matching, not string-identity matching.** `citation.url` is already run through
the M0 `normalize_url` helper before it is persisted (`measurement/runner.py`), which strips only
`utm_*` query params, the fragment, and a trailing slash. A landing URL reaching the lead-capture
pixel, though, can carry *any* tracking param a redirect chain added (a bare `utm=`, click ids,
session tokens, ...) that `normalize_url` alone won't strip and that has nothing to do with which
page was visited. So matching here reuses `normalize_url` for the rules it *is* the source of
truth for (host case, trailing slash, fragment) and then additionally drops the query string
entirely on both sides, keying the match on scheme+host+path: "did this visit land on this page",
not "does this visit's exact querystring equal the citation's".

**Idempotent upsert.** Keyed on `(session_id, method="citation_linked")` -- the same convention
`referral.py`'s `link_direct` uses for its own `method="direct"` links (T06 task spec) -- so
re-running over the same window updates the one link per session in place rather than duplicating
it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from gw_geo.common.db import AttributionLink, Citation
from gw_geo.common.db import Session as SessionRow
from gw_geo.common.db import TenantScopedSession
from gw_geo.measurement.parse import normalize_url


def _inclusive_window(since: str, until: str) -> tuple[datetime, datetime]:
    """`[since, until]` inclusive UTC day bounds as a half-open `(start, end)` datetime range.

    Same `YYYY-MM-DD`, inclusive-ends convention as `measurement/feed.py`'s
    `_inclusive_date_bounds` / `attribution/holdout.py`'s `_inclusive_window` (TRD §5).
    """
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


def _page_key(url: str) -> str:
    """Reduce `url` to a page-identity key: M0-normalized, then the query dropped entirely.

    See the module docstring for why this is deliberately coarser than `normalize_url` alone.
    """
    scheme, netloc, path, _query, _fragment = urlsplit(normalize_url(url))
    return urlunsplit((scheme, netloc, path, "", ""))


def _select_citation(candidates: list[Citation], session_engine: str | None) -> Citation | None:
    """Pick which citation to credit among same-page-key `candidates` (usually just one).

    With no known `session_engine`, the first candidate is used. With a known engine, only a
    same-engine candidate is credited -- a page cited by a *different* engine than the one that
    already, independently, drove this session is not evidence of what drove it.
    """
    if not candidates:
        return None
    if session_engine is None:
        return candidates[0]
    for candidate in candidates:
        if candidate.engine == session_engine:
            return candidate
    return None


def _upsert_link(
    session: TenantScopedSession,
    *,
    tenant_id: str,
    brand_id: str,
    session_row: SessionRow,
    citation: Citation,
) -> AttributionLink:
    """Idempotent upsert of the one `citation_linked` link for `session_row`, keyed on
    `(session_id, method)` (see module docstring).
    """
    existing = (
        session.query(AttributionLink)
        .filter(
            AttributionLink.brand_id == brand_id,
            AttributionLink.session_id == session_row.id,
            AttributionLink.method == "citation_linked",
        )
        .one_or_none()
    )
    if existing is not None:
        existing.citation_id = citation.id
        existing.prompt_id = citation.prompt_id
        existing.engine = citation.engine
        existing.confidence = "high"
        return existing

    link = AttributionLink(
        id=uuid4().hex,
        tenant_id=tenant_id,
        brand_id=brand_id,
        lead_id=None,
        session_id=session_row.id,
        citation_id=citation.id,
        prompt_id=citation.prompt_id,
        engine=citation.engine,
        method="citation_linked",
        confidence="high",
        value_usd=None,
    )
    session.add(link)
    return link


def link_citations(
    session: TenantScopedSession,
    *,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
) -> list[AttributionLink]:
    """Mechanism 2 (TRD §6 #2, m2-design §2.3): citation-to-page linkage.

    Loads every `brand_id` session in `[since, until]` and every `brand_id` citation (tenant-scoped
    via `session`, TRD §7), matches each session's `landing_url` to a `citation.url` by page
    identity (module docstring), and idempotently upserts an
    `attribution_link(method="citation_linked", confidence="high", citation_id, prompt_id, engine)`
    per match. Returns the upserted links, `[]` if none matched. Raises `ValueError` if `session`
    is scoped to a different tenant than `tenant_id`.
    """
    if session.tenant_id != tenant_id:
        raise ValueError(f"session is scoped to tenant_id={session.tenant_id!r}, not {tenant_id!r}")

    start, end = _inclusive_window(since, until)

    citations = session.query(Citation).filter(Citation.brand_id == brand_id).all()
    citations_by_key: dict[str, list[Citation]] = {}
    for citation in citations:
        citations_by_key.setdefault(_page_key(citation.url), []).append(citation)

    sessions = (
        session.query(SessionRow)
        .filter(
            SessionRow.brand_id == brand_id,
            SessionRow.ts >= start,
            SessionRow.ts < end,
        )
        .all()
    )

    links: list[AttributionLink] = []
    for session_row in sessions:
        candidates = citations_by_key.get(_page_key(session_row.landing_url), [])
        matched_citation = _select_citation(candidates, session_row.engine)
        if matched_citation is None:
            continue
        links.append(
            _upsert_link(
                session,
                tenant_id=tenant_id,
                brand_id=brand_id,
                session_row=session_row,
                citation=matched_citation,
            )
        )

    session.commit()
    return links
