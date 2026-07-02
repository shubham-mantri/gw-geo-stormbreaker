"""Pipeline aggregation (m2-design §2.6, TRD §6, PRD §6.2/§13): the single function the
``GET /brands/{id}/pipeline`` endpoint (ui-spec §3.6/§6) calls.

Composes the four attribution mechanisms into one board-ready revenue view -- with the **method
breakdown + confidence note front and centre**. This is the honesty / anti-overclaim backbone of
the product (m2-design §1 "non-overclaim rule", PRD §13): the UI never shows a bare attribution
number, only the method mix that produced it, and only holdout incrementality is presented as a
causal claim.

**Reads persisted links; does not re-run the writers.** The three fuzzy mechanisms
(``referral.link_direct`` = T06, ``linkage.link_citations`` = T07, ``assisted.assisted_credit`` =
T08) have already written their ``attribution_link`` rows by the time a dashboard request lands;
this module *aggregates* those rows (grouped by ``method``) rather than re-computing them. The one
mechanism that produces no link -- holdout incrementality (``holdout.measure_incrementality`` = T09,
the controlled experiment) -- is called live here for the ``lift`` figure.

**Dedup per lead (no double-count).** A single lead can carry several links: a ``direct`` link (T06,
keyed on its session), a standalone ``citation_linked`` link (T07, keyed on the same session, no
``lead_id`` of its own), and an ``assisted`` link (T08, keyed on the lead). Each lead is counted
**once**, and the lead is assigned to its **strongest** method (``direct`` > ``citation_linked`` >
``assisted``) for the breakdown, so the three lead-buckets are disjoint and sum exactly to
``influenced``. The value counted is the lead's **full** value for a ``direct``/``citation_linked``
lead (an observed AI referral), but the **probability-weighted** value ``assisted.py`` persisted
(``lead.value_usd * min(r, 1.0)``) for an ``assisted``-strongest lead -- honouring, rather than
discarding, that weighting (PRD §13 anti-overclaim: a low-confidence modelled lift must never be
booked at full lead value). ``attributed`` is the defensible subset -- leads whose strongest
method is ``direct`` or ``citation_linked`` -- and therefore is always ``<= influenced`` (asserted
defensively as well).

**A lead is "touched" by a method** either directly (a link's ``lead_id`` is this lead) or via its
session (a link's ``session_id`` is this lead's ``session_id``) -- the latter is how the per-session
``citation_linked`` links, which carry no ``lead_id``, reach the lead that converted on that visit.

**``holdout_incremental``** is the causal dollars *implied* by the measured lift, not a lead-bucket:
it does not participate in the disjoint sum above. It is estimated as
``influenced * lift / (1 + lift)`` for a positive relative lift (the standard "incrementality
fraction" -- the share of conversions above the holdout baseline), and ``0.0`` otherwise. Basing it
on ``influenced`` (rather than ``attributed``) and this exact fraction are modelling choices, not a
TRD-pinned contract -- flagged in the task report for the orchestrator/user to confirm.

**Scoping.** Unlike the mechanism writers (which take an already-``TenantScopedSession``), this
entry point takes a raw ``Session`` plus an explicit ``tenant_id`` and builds the
``TenantScopedSession`` itself (TRD §7): every read below is tenant-scoped, so a second tenant sees
an all-zero view of the same database rather than another tenant's numbers. ``tenant_id`` here comes
from the caller's JWT (the API layer never accepts a client-supplied tenant), so this is the natural
request-entry shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.holdout import measure_incrementality
from gw_geo.common.db import AttributionLink, HoldoutCohort, Lead, Prompt, TenantScopedSession

# The three fuzzy lead-level methods, strongest -> weakest (m2-design §2, PRD §6.2). Holdout
# incrementality is deliberately *not* here: it produces no per-lead link, it is a separate causal
# figure. ``low`` etc. are confidence tiers, not methods; the method vocabulary is exactly these
# three plus ``holdout_incremental`` (common/db.py::AttributionLink).
_METHOD_RANK: dict[str, int] = {"direct": 0, "citation_linked": 1, "assisted": 2}
_LEAD_METHODS = frozenset(_METHOD_RANK)
_DEFENSIBLE_METHODS = frozenset({"direct", "citation_linked"})

# The honesty disclosure (PRD §13, m2-design §1): holdout is the only causal claim; direct and
# citation-linked are defensible but correlational; assisted is low-confidence. Kept as a single
# plain-language string so the dashboard can render it verbatim beneath the method breakdown.
_CONFIDENCE_NOTE = (
    "Attribution methods are shown separately by confidence, and no single number is presented "
    "without this breakdown. Holdout incrementality (the lift figure) is the only causal "
    "measurement here: it comes from a controlled holdout experiment that compares an "
    "un-optimized cohort against the optimized remainder. Direct-referral and citation-linked "
    "dollars are defensible but correlational -- they show that an AI engine referred the visit "
    "(and, for citation-linked, which cited answer drove it), not that AI search caused the "
    "purchase. Assisted dollars are low-confidence, probabilistic estimates (self-reported or "
    "modelled from branded-search correlation) and should be read as directional only."
)


def _inclusive_window(since: str, until: str) -> tuple[datetime, datetime]:
    """``[since, until]`` inclusive UTC day bounds as a half-open ``(start, end)`` datetime range.

    Same ``YYYY-MM-DD``, inclusive-ends convention as ``measurement/feed.py`` and the other
    ``attribution/`` modules' ``_inclusive_window`` (TRD §5).
    """
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


def _strongest_method(methods: set[str]) -> str:
    """The strongest method in ``methods`` per ``_METHOD_RANK`` (``direct`` > ``citation_linked`` >
    ``assisted``). ``methods`` is always non-empty when called (a lead untouched by any mechanism is
    filtered out upstream)."""
    return min(methods, key=lambda method: _METHOD_RANK[method])


def _resolve_holdout_cohort(
    scoped: TenantScopedSession, brand_id: str
) -> HoldoutCohort | None:
    """The brand's active holdout cohort, or ``None``.

    "The active/holdout one" (task spec) is read as an ``is_holdout=True`` cohort; if several
    exist, the most recently started wins. When the brand has no holdout arm at all this returns
    ``None`` and the caller reports ``lift = 0.0`` rather than calling ``measure_incrementality``
    (which would raise on a missing cohort).
    """
    return (
        scoped.query(HoldoutCohort)
        .filter(HoldoutCohort.brand_id == brand_id, HoldoutCohort.is_holdout.is_(True))
        .order_by(HoldoutCohort.started_at.desc())
        .first()
    )


def _holdout_lift(
    scoped: TenantScopedSession, *, tenant_id: str, brand_id: str, since: str, until: str
) -> float:
    """Relative incremental ``lift_pct`` from the brand's holdout experiment (the only causal
    figure), or ``0.0`` when the brand has no holdout cohort."""
    cohort = _resolve_holdout_cohort(scoped, brand_id)
    if cohort is None:
        return 0.0
    result = measure_incrementality(
        scoped, tenant_id=tenant_id, brand_id=brand_id, cohort_id=cohort.id, since=since, until=until
    )
    return result.lift_pct


def _build_top_answers(
    scoped: TenantScopedSession, leads_by_prompt: dict[str, int], value_by_prompt: dict[str, float]
) -> list[dict[str, Any]]:
    """``top_answers`` (ui-spec §3.6 "top converting AI answers"): citation-linked-driven leads
    grouped by prompt, prompt ids resolved to their human-readable text, sorted by value then leads
    descending (ties broken by prompt id for determinism)."""
    if not leads_by_prompt:
        return []
    prompt_ids = list(leads_by_prompt)
    text_by_id = {
        prompt.id: prompt.text
        for prompt in scoped.query(Prompt).filter(Prompt.id.in_(prompt_ids)).all()
    }
    ordered = sorted(
        prompt_ids,
        key=lambda pid: (-value_by_prompt[pid], -leads_by_prompt[pid], pid),
    )
    return [
        {
            "prompt": text_by_id.get(pid, pid),
            "leads": leads_by_prompt[pid],
            "value": value_by_prompt[pid],
        }
        for pid in ordered
    ]


def pipeline_view(
    session: SASession,
    *,
    tenant_id: str,
    brand_id: str,
    since: str,
    until: str,
    visibility_series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compose the four attribution mechanisms into the ui-spec §6 ``GET /brands/{id}/pipeline``
    shape.

    Returns::

        {
          "influenced": float,        # $ touched by ANY mechanism (dedup per lead; assisted
                                      #   leads contribute their probability-weighted value)
          "attributed": float,        # $ whose strongest method is direct|citation_linked
          "leads": int,               # count of touched leads
          "lift": float,              # holdout incrementality lift_pct -- the only causal number
          "top_answers": [{"prompt": str, "leads": int, "value": float}],
          "method_breakdown": {"direct": float, "citation_linked": float,
                               "assisted": float, "holdout_incremental": float},
          "confidence_note": str,     # plain-language honesty disclosure (never empty)
        }

    ``session`` is a raw SQLAlchemy ``Session``; it is scoped to ``tenant_id`` internally so every
    read is tenant-safe (TRD §7). ``visibility_series`` is accepted for signature parity with the
    ``/overview`` composition (which has the branded-lift series to hand) but is not needed here:
    the branded-lift weighting was already applied by ``assisted.py`` when it persisted each
    assisted link's ``value_usd``, and this function reads that persisted weighted value directly
    for any assisted-strongest lead (rather than the lead's full value) -- so it never needs to
    re-derive the correlation.
    """
    scoped = TenantScopedSession(session, tenant_id)
    start, end = _inclusive_window(since, until)

    leads = (
        scoped.query(Lead)
        .filter(Lead.brand_id == brand_id, Lead.ts >= start, Lead.ts < end)
        .all()
    )
    links = scoped.query(AttributionLink).filter(AttributionLink.brand_id == brand_id).all()

    # Map each fuzzy link onto the lead(s) it touches: directly via lead_id, or via the session it
    # was written for (how per-session citation_linked links, which carry no lead_id, reach a lead).
    methods_by_lead: dict[str, set[str]] = {}
    methods_by_session: dict[str, set[str]] = {}
    prompt_by_session: dict[str, str] = {}  # session -> citation_linked prompt (drives top_answers)
    # assisted.py persists a *probability-weighted* credit (link.value_usd = lead.value_usd *
    # min(r, 1.0)); an assisted-strongest lead is booked at THAT value, not its full lead value.
    assisted_value_by_lead: dict[str, float] = {}
    for link in links:
        if link.method not in _LEAD_METHODS:
            continue
        if link.lead_id is not None:
            methods_by_lead.setdefault(link.lead_id, set()).add(link.method)
            if link.method == "assisted" and link.value_usd is not None:
                assisted_value_by_lead[link.lead_id] = link.value_usd
        if link.session_id is not None:
            methods_by_session.setdefault(link.session_id, set()).add(link.method)
            if link.method == "citation_linked" and link.prompt_id is not None:
                prompt_by_session[link.session_id] = link.prompt_id

    influenced = 0.0
    buckets = {"direct": 0.0, "citation_linked": 0.0, "assisted": 0.0}
    touched_leads = 0
    leads_by_prompt: dict[str, int] = {}
    value_by_prompt: dict[str, float] = {}

    for lead in leads:
        methods = set(methods_by_lead.get(lead.id, set()))
        if lead.session_id is not None:
            methods |= methods_by_session.get(lead.session_id, set())
        if not methods:
            continue  # untouched by every mechanism -> not AI-influenced, excluded entirely

        touched_leads += 1
        value = lead.value_usd if lead.value_usd is not None else 0.0
        strongest = _strongest_method(methods)
        # An assisted-strongest lead contributes only the probability-weighted credit the assisted
        # writer persisted (assisted.py: lead.value_usd * min(r, 1.0)) -- never the full lead value,
        # which would silently discard the branded-lift weighting and inflate a low-confidence
        # modelled figure to 100c on the dollar (PRD §13 anti-overclaim). Direct / citation_linked
        # leads are observed AI referrals and keep their full value. (Falls back to full value only
        # for the degenerate case of an assisted method reaching a lead with no persisted assisted
        # link value of its own, e.g. a shared session -- vanishingly rare, and never an inflation.)
        credit = assisted_value_by_lead.get(lead.id, value) if strongest == "assisted" else value
        influenced += credit
        buckets[strongest] += credit  # counted once, under the strongest method

        # top_answers credits the cited answer that drove the visit, regardless of which method
        # ended up strongest for the lead's own bucket. It uses the full lead value: any lead that
        # reaches top_answers has a citation_linked link on its session, so its strongest method is
        # direct or citation_linked (never assisted) -- so `credit == value` for these leads anyway.
        if lead.session_id is not None and lead.session_id in prompt_by_session:
            prompt_id = prompt_by_session[lead.session_id]
            leads_by_prompt[prompt_id] = leads_by_prompt.get(prompt_id, 0) + 1
            value_by_prompt[prompt_id] = value_by_prompt.get(prompt_id, 0.0) + value

    attributed = sum(buckets[method] for method in _DEFENSIBLE_METHODS)
    attributed = min(attributed, influenced)  # honesty invariant, structurally true, held defensively

    lift = _holdout_lift(scoped, tenant_id=tenant_id, brand_id=brand_id, since=since, until=until)
    holdout_incremental = influenced * lift / (1.0 + lift) if lift > 0.0 else 0.0

    return {
        "influenced": influenced,
        "attributed": attributed,
        "leads": touched_leads,
        "lift": lift,
        "top_answers": _build_top_answers(scoped, leads_by_prompt, value_by_prompt),
        "method_breakdown": {
            "direct": buckets["direct"],
            "citation_linked": buckets["citation_linked"],
            "assisted": buckets["assisted"],
            "holdout_incremental": holdout_incremental,
        },
        "confidence_note": _CONFIDENCE_NOTE,
    }
