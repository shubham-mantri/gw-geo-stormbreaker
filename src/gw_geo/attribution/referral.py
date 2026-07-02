"""Attribution mechanism 1 (TRD §6 #1, m2-design §2.2): direct referral capture -- the strongest
signal in the four-mechanism stack. A static, versioned map of AI-engine referrer hosts, a pure
classifier over `session.referrer`/`session.utm`, and a tenant-scoped linker that turns a
classified session into an `attribution_link(method="direct", confidence="high")`.

**Host matching, not domain normalization.** `classify_referrer` matches the referrer's lowercased
host directly against `AI_ENGINE_REFERRERS`. It does *not* strip a leading `www.` the way
`measurement/parse.py`'s `domain_of` does -- the map instead lists both bare and `www.`-prefixed
hosts explicitly (`perplexity.ai` / `www.perplexity.ai`) wherever an engine is known to serve
either. That keeps this classifier a simple, dependency-free lookup and keeps `chatgpt.com` /
`chat.openai.com` (genuinely different hosts, not a www-variant pair) unambiguous. (Mechanism 2,
`attribution/linkage.py`, is the one that reuses M0's URL normalization -- for matching
`landing_url` to a cited page, a different problem.)

**`utm_source` fallback.** Some AI engines omit the `Referer` header entirely, or it doesn't
survive a redirect chain; `classify_referrer` then falls back to `utm.get("utm_source")` when it
names one of the engines already present as a value in `AI_ENGINE_REFERRERS` (the "engine set"),
so a session tagged `utm_source=gemini` still classifies with no/opaque referrer.

**Session -> engine bookkeeping.** `link_direct` also writes the detected engine back onto
`session.engine`, closing the loop described by `Session`'s own docstring ("`engine`, nullable
until classified") and `attribution/ingest.py`'s tests ("engine classification is a later
mechanism (T06), not ingestion"). This is additive -- it does not change any `attribution_link`
field and does not affect which sessions produce a link -- flagged in the task report as an
interpretation beyond the literal T06 interface, for the orchestrator/user to confirm.

**Value/lead linkage.** A link is written per *session* (an AI-referred visit is a signal worth
recording whether or not it ever converted), but if the visitor's session went on to produce a
`lead`, that lead's id/`value_usd` ride along on the link -- the most-recent lead tied to the
session, mirroring `ingest.py`'s "most-recent" convention (there in the session-per-visitor
direction, here in the lead-per-session direction). A session with no lead yet gets a link with
`lead_id=None, value_usd=None`: an *influenced*, not-yet-*attributed*, signal (PRD §6.2).

**Idempotent upsert.** Keyed on `(session_id, method="direct")`: re-running `link_direct` over a
window that includes an already-linked session updates that link's `engine`/`lead_id`/`value_usd`
in place rather than creating a duplicate row.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from gw_geo.common.db import AttributionLink, Lead, Session, TenantScopedSession

AI_ENGINE_REFERRERS: dict[str, str] = {
    "chatgpt.com": "chatgpt",
    "chat.openai.com": "chatgpt",
    "perplexity.ai": "perplexity",
    "www.perplexity.ai": "perplexity",
    "gemini.google.com": "gemini",
    "copilot.microsoft.com": "copilot",
    "claude.ai": "claude",
    "grok.com": "grok",
    "x.com": "grok",
}

_AI_ENGINES = set(AI_ENGINE_REFERRERS.values())


def _host_of(referrer: str) -> str:
    """Lowercased host from a referrer URL, with any port stripped.

    Deliberately does *not* strip a leading `www.` -- see module docstring.
    """
    netloc = urlsplit(referrer).netloc.lower()
    return netloc.partition(":")[0]


def classify_referrer(referrer: str | None, utm: dict[str, str]) -> str | None:
    """Detect the AI engine (if any) that referred a session.

    Host match on `referrer` against `AI_ENGINE_REFERRERS` first; falls back to `utm["utm_source"]`
    when it names one of the engines in that map's value set. Returns the engine name
    (e.g. `"chatgpt"`) or `None` for a non-AI/organic/unrecognized referrer.
    """
    if referrer:
        engine = AI_ENGINE_REFERRERS.get(_host_of(referrer))
        if engine is not None:
            return engine
    utm_source = utm.get("utm_source", "").lower()
    if utm_source in _AI_ENGINES:
        return utm_source
    return None


def _inclusive_window(since: str, until: str) -> tuple[datetime, datetime]:
    """`[since, until]` inclusive UTC day bounds as a half-open `(start, end)` datetime range.

    Same `YYYY-MM-DD`, inclusive-ends convention as `measurement/feed.py`'s
    `_inclusive_date_bounds` and `attribution/holdout.py`'s `_inclusive_window` (TRD §5).
    """
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


def _latest_lead(session: TenantScopedSession, session_id: str) -> Lead | None:
    """The most-recently-created `lead` tied to `session_id`, if any (tenant-scoped)."""
    return (
        session.query(Lead)
        .filter(Lead.session_id == session_id)
        .order_by(Lead.ts.desc())
        .first()
    )


def _upsert_direct_link(
    session: TenantScopedSession,
    *,
    tenant_id: str,
    brand_id: str,
    session_row: Session,
    engine: str,
    lead: Lead | None,
) -> AttributionLink:
    """Idempotent upsert of a `direct`/`high` `attribution_link`, keyed on `(session_id, method)`."""
    existing = (
        session.query(AttributionLink)
        .filter(
            AttributionLink.session_id == session_row.id,
            AttributionLink.method == "direct",
        )
        .first()
    )
    lead_id = lead.id if lead is not None else None
    value_usd = lead.value_usd if lead is not None else None

    if existing is not None:
        existing.engine = engine
        existing.confidence = "high"
        existing.lead_id = lead_id
        existing.value_usd = value_usd
        return existing

    link = AttributionLink(
        id=uuid4().hex,
        tenant_id=tenant_id,
        brand_id=brand_id,
        lead_id=lead_id,
        session_id=session_row.id,
        citation_id=None,
        prompt_id=None,
        engine=engine,
        method="direct",
        confidence="high",
        value_usd=value_usd,
    )
    session.add(link)
    return link


def link_direct(
    session: TenantScopedSession,
    *,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
) -> list[AttributionLink]:
    """Mechanism 1 (TRD §6 #1): direct referral capture.

    Tenant-scoped read of `session` rows for `brand_id` in `[since, until]`; for each session with
    a classifiable AI referrer (`classify_referrer`), upserts a `direct`/`high` `attribution_link`
    (module docstring covers value/lead linkage and idempotency) and stamps the detected engine
    back onto `session.engine`. Sessions with no AI referrer are left untouched and produce no
    link. Raises `ValueError` if `session` is scoped to a different tenant than `tenant_id`
    (TRD §7).
    """
    if session.tenant_id != tenant_id:
        raise ValueError(f"session is scoped to tenant_id={session.tenant_id!r}, not {tenant_id!r}")

    start, end = _inclusive_window(since, until)
    sessions = (
        session.query(Session)
        .filter(Session.brand_id == brand_id, Session.ts >= start, Session.ts < end)
        .all()
    )

    links: list[AttributionLink] = []
    for session_row in sessions:
        engine = classify_referrer(session_row.referrer, session_row.utm)
        if engine is None:
            continue
        session_row.engine = engine
        lead = _latest_lead(session, session_row.id)
        links.append(
            _upsert_direct_link(
                session,
                tenant_id=tenant_id,
                brand_id=brand_id,
                session_row=session_row,
                engine=engine,
                lead=lead,
            )
        )

    session.commit()
    return links
