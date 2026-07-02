"""Attribution mechanism 3 (TRD §6 #3, m2-design §2.4, PRD §6.2 #3): assisted modeling --
correlational, **always low-confidence**. Unlike direct referral (mechanism 1) and citation
linkage (mechanism 2), this mechanism never observes an AI engine driving a session directly; it
only ever produces two flavours of *inference*:

* **Self-report (`confidence="reported"`).** A lead answers "how did you hear about us" and names
  an AI engine (`lead.self_reported_source`) -- a first-party claim, not an instrumented signal, so
  it is credited but never promoted above `reported`.
* **Branded-lift correlation (`confidence="modeled"`).** Leads that arrived via branded search or
  direct navigation (i.e. *not* through a mechanism-1-classified AI referrer) are correlated, as a
  daily count, against `visibility_series` (`feed.share_of_voice_trend`, m1-design §5). A positive
  Pearson `r` (`branded_lift_correlation`) is used only as a **probabilistic weight** on how much of
  that branded/direct lead's value to credit -- never as proof that any *specific* lead was
  AI-influenced.

**Honesty rule (PRD §13; m2-design §1 "non-overclaim rule").** Holdout incrementality
(`attribution/holdout.py`) is the *only* mechanism in this package that supports a causal claim;
direct referral, citation linkage, and this module are all correlational. `_ASSISTED_CONFIDENCES`
is the hard ceiling this module enforces: an assisted link's `confidence` is never `high` or
`medium`, no matter how strong the observed correlation is -- `_upsert_assisted_link` raises rather
than ever writing one.

**Self-report matching reuses T06's map, not a new one.** `_self_report_lookup` builds a
case-insensitive dict from `AI_ENGINE_REFERRERS` (`attribution/referral.py`): both its host keys
(e.g. `"chatgpt.com"`) and its engine-slug values (e.g. `"chatgpt"`) resolve to the canonical slug,
so a lead can report either the product name ("ChatGPT") -- the expected case, e.g. a "how did you
hear about us" dropdown -- or, less commonly, paste a bare host. No second, independently-maintained
engine list is introduced.

**The "branded/direct" population (`_is_branded_or_direct`).** A lead counts as branded/direct when
its session was never classified as AI-referred by mechanism 1 (`session.engine is None`), or it
has no tracked session at all (e.g. an offline/CRM-entered lead) -- in both cases there is no
referrer that could have named an AI engine, which is exactly the "saw the brand in an AI answer,
came back later via branded search/direct" population this mechanism exists to model (PRD §6.2 #3).
Self-report and branded-lift are mutually exclusive per lead: a lead whose self-report already
matched is excluded from the branded-lift population, so it is never double-credited under both
confidence tiers in one `assisted_credit` call.

**Zero-filled daily alignment.** `_daily_lead_counts` zero-fills every calendar day in
`[since, until]`, not just days with at least one lead -- a day where visibility was high but the
branded/direct lead count was genuinely zero is real evidence *against* a lift hypothesis, and
silently dropping it (by only emitting days that had a lead) would bias `branded_lift_correlation`
toward a false positive. `branded_lift_correlation` itself then aligns on the *intersection* of
both series' dates (a date missing from `visibility_series` contributes nothing either way) and
returns `0.0` -- "no evidence either way" -- for fewer than two aligned points or a constant series
on either axis, both of which leave Pearson `r` mathematically undefined, rather than raising or
propagating `nan` (mirrors `holdout.py`'s "finite, directionally honest" fallback convention).

**Modeled credit is a probabilistic weight, not a headcount.** When `r > 0.0`, every branded/direct
lead in the window gets a `modeled` link; each one's `value_usd` is scaled by `min(r, 1.0)` (never
grossed up past the lead's actual value) rather than crediting the full amount -- the correlation
strength *is* the confidence, expressed as a fraction of value rather than as a discrete label.
`r <= 0.0` (no positive lift signal) produces no modeled links at all: no evidence, no credit.

**`engine` for a modeled link.** `visibility_series` (`feed.share_of_voice_trend`) is already
blended "across engines/geo/persona" (that function's own docstring) before it ever reaches this
module, so a `modeled` link has no single engine to name the way a `direct`/`citation_linked`/
`reported` link does. `_MODELED_ENGINE = "aggregate"` is a sentinel standing in for "AI search
broadly, engine unknown" (`AttributionLink.engine` is non-nullable, TRD §4). This is an
interpretation beyond the literal TRD/m2-design interface (silent on this point) -- flagged here for
the orchestrator/user to confirm it stays consistent with however `pipeline.py` (T10) ends up
reading per-engine views.

**Idempotent upsert.** Keyed on `(lead_id, method="assisted")` -- the same per-(row, method)
convention `referral.py`/`linkage.py` use for their own methods -- so re-running `assisted_credit`
over a window that includes an already-linked lead updates that one row's `engine`/`confidence`/
`value_usd` in place rather than duplicating it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from scipy.stats import pearsonr  # type: ignore[import-untyped]

from gw_geo.attribution.referral import AI_ENGINE_REFERRERS
from gw_geo.common.db import AttributionLink, Lead
from gw_geo.common.db import Session as SessionRow
from gw_geo.common.db import TenantScopedSession

# See module docstring ("`engine` for a modeled link").
_MODELED_ENGINE = "aggregate"

# The honesty-rule ceiling (PRD §13): an `assisted` link's `confidence` may never be `high`/
# `medium`. `"low"` is one of `AttributionLink`'s five documented confidence tiers (common/db.py)
# but is not currently produced by this module; it is included here as an allowed value, not a
# used one. Enforced defensively in `_upsert_assisted_link`.
_ASSISTED_CONFIDENCES = frozenset({"reported", "modeled", "low"})


def _self_report_lookup() -> dict[str, str]:
    """Case-insensitive `"how did you hear about us"` text -> canonical AI-engine slug, built from
    `AI_ENGINE_REFERRERS` (T06). See module docstring.
    """
    lookup: dict[str, str] = {}
    for host, engine in AI_ENGINE_REFERRERS.items():
        lookup[host.lower()] = engine
        lookup.setdefault(engine.lower(), engine)
    return lookup


_SELF_REPORT_ENGINES = _self_report_lookup()


def _match_self_reported_engine(source: str | None) -> str | None:
    """The AI engine `source` names, if any (case-insensitive, whitespace-trimmed); `None` for an
    empty/unrecognized source.
    """
    if not source:
        return None
    return _SELF_REPORT_ENGINES.get(source.strip().lower())


def _inclusive_window(since: str, until: str) -> tuple[datetime, datetime]:
    """`[since, until]` inclusive UTC day bounds as a half-open `(start, end)` datetime range.

    Same `YYYY-MM-DD`, inclusive-ends convention as `measurement/feed.py`'s
    `_inclusive_date_bounds` and `attribution/referral.py`/`linkage.py`'s `_inclusive_window`
    (TRD §5).
    """
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


def branded_lift_correlation(
    visibility_series: list[dict[str, Any]], lead_series: list[dict[str, Any]]
) -> float:
    """Pearson `r` in `[-1, 1]` between `visibility_series`'s `share_of_voice` and `lead_series`'s
    `leads`, aligned by their shared `date` key (`scipy.stats.pearsonr`).

    Correlation, never causation (module docstring / PRD §13) -- this is the entire evidentiary
    basis for `assisted_credit`'s `modeled` tier. Returns `0.0` ("no evidence either way") for
    fewer than two aligned dates or a constant series on either axis, both of which leave Pearson
    `r` mathematically undefined (`scipy` would raise/emit `nan`); this keeps the function total
    and its output always usable as a weight.
    """
    vis_by_date = {row["date"]: row["share_of_voice"] for row in visibility_series}
    lead_by_date = {row["date"]: row["leads"] for row in lead_series}
    shared_dates = sorted(set(vis_by_date) & set(lead_by_date))

    if len(shared_dates) < 2:
        return 0.0

    xs = [float(vis_by_date[date]) for date in shared_dates]
    ys = [float(lead_by_date[date]) for date in shared_dates]

    if len(set(xs)) == 1 or len(set(ys)) == 1:
        return 0.0

    r, _p_value = pearsonr(xs, ys)
    return float(r)


def _daily_lead_counts(leads: list[Lead], *, since: str, until: str) -> list[dict[str, Any]]:
    """`[{"date": "YYYY-MM-DD", "leads": n}, ...]`, one zero-filled entry per calendar day in
    `[since, until]` -- the `lead_series` shape `branded_lift_correlation` expects. See module
    docstring ("Zero-filled daily alignment") for why zero-count days matter.
    """
    counts: dict[str, int] = {}
    for lead in leads:
        date = lead.ts.strftime("%Y-%m-%d")
        counts[date] = counts.get(date, 0) + 1

    start = datetime.strptime(since, "%Y-%m-%d")
    end = datetime.strptime(until, "%Y-%m-%d")
    series: list[dict[str, Any]] = []
    day = start
    while day <= end:
        key = day.strftime("%Y-%m-%d")
        series.append({"date": key, "leads": counts.get(key, 0)})
        day += timedelta(days=1)
    return series


def _is_branded_or_direct(lead: Lead, sessions_by_id: dict[str, SessionRow]) -> bool:
    """True when `lead` is part of the branded/direct population this mechanism models: its
    session was never classified as AI-referred by mechanism 1, or it has no tracked session at
    all. See module docstring.
    """
    if lead.session_id is None:
        return True
    session_row = sessions_by_id.get(lead.session_id)
    return session_row is None or session_row.engine is None


def _upsert_assisted_link(
    session: TenantScopedSession,
    *,
    tenant_id: str,
    brand_id: str,
    lead: Lead,
    engine: str,
    confidence: str,
    value_usd: float | None,
) -> AttributionLink:
    """Idempotent upsert of the one `assisted` link for `lead`, keyed on `(lead_id, method)` (see
    module docstring). Raises `ValueError` if `confidence` is ever outside
    `_ASSISTED_CONFIDENCES` -- the PRD §13 honesty-rule ceiling this module must never breach.
    """
    if confidence not in _ASSISTED_CONFIDENCES:
        raise ValueError(
            f"assisted attribution confidence must be one of {sorted(_ASSISTED_CONFIDENCES)}, "
            f"got {confidence!r} (PRD §13: assisted credit is never high/medium confidence)"
        )

    existing = (
        session.query(AttributionLink)
        .filter(AttributionLink.lead_id == lead.id, AttributionLink.method == "assisted")
        .one_or_none()
    )
    if existing is not None:
        existing.engine = engine
        existing.confidence = confidence
        existing.session_id = lead.session_id
        existing.value_usd = value_usd
        return existing

    link = AttributionLink(
        id=uuid4().hex,
        tenant_id=tenant_id,
        brand_id=brand_id,
        lead_id=lead.id,
        session_id=lead.session_id,
        citation_id=None,
        prompt_id=None,
        engine=engine,
        method="assisted",
        confidence=confidence,
        value_usd=value_usd,
    )
    session.add(link)
    return link


def assisted_credit(
    session: TenantScopedSession,
    *,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
    visibility_series: list[dict[str, Any]],
) -> list[AttributionLink]:
    """Mechanism 3 (TRD §6 #3, m2-design §2.4): assisted modeling -- correlational, always
    low-confidence.

    Two independent passes over `brand_id`'s leads in `[since, until]` (tenant-scoped via
    `session`, TRD §7):

    1. **Self-report:** any lead whose `self_reported_source` names an AI engine (module
       docstring) gets a `reported` link.
    2. **Branded-lift:** every remaining lead in the branded/direct population (module docstring)
       is correlated, as a daily count, against `visibility_series`; a positive
       `branded_lift_correlation` produces a `modeled` link per lead, `value_usd` scaled by that
       correlation as a probabilistic weight (module docstring). A non-positive correlation
       produces no modeled links at all.

    Raises `ValueError` if `session` is scoped to a different tenant than `tenant_id` (TRD §7).
    """
    if session.tenant_id != tenant_id:
        raise ValueError(f"session is scoped to tenant_id={session.tenant_id!r}, not {tenant_id!r}")

    start, end = _inclusive_window(since, until)
    leads = (
        session.query(Lead)
        .filter(Lead.brand_id == brand_id, Lead.ts >= start, Lead.ts < end)
        .all()
    )

    session_ids = {lead.session_id for lead in leads if lead.session_id is not None}
    sessions_by_id: dict[str, SessionRow] = {}
    if session_ids:
        sessions_by_id = {
            row.id: row
            for row in session.query(SessionRow).filter(SessionRow.id.in_(session_ids)).all()
        }

    links: list[AttributionLink] = []
    reported_lead_ids: set[str] = set()

    for lead in leads:
        engine = _match_self_reported_engine(lead.self_reported_source)
        if engine is None:
            continue
        reported_lead_ids.add(lead.id)
        links.append(
            _upsert_assisted_link(
                session,
                tenant_id=tenant_id,
                brand_id=brand_id,
                lead=lead,
                engine=engine,
                confidence="reported",
                value_usd=lead.value_usd,
            )
        )

    branded_candidates = [
        lead
        for lead in leads
        if lead.id not in reported_lead_ids and _is_branded_or_direct(lead, sessions_by_id)
    ]
    lead_series = _daily_lead_counts(branded_candidates, since=since, until=until)
    r = branded_lift_correlation(visibility_series, lead_series)

    if r > 0.0:
        weight = min(r, 1.0)
        for lead in branded_candidates:
            raw_value = lead.value_usd
            value_usd = raw_value * weight if raw_value is not None else None
            links.append(
                _upsert_assisted_link(
                    session,
                    tenant_id=tenant_id,
                    brand_id=brand_id,
                    lead=lead,
                    engine=_MODELED_ENGINE,
                    confidence="modeled",
                    value_usd=value_usd,
                )
            )

    session.commit()
    return links
