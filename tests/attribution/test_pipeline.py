"""Tests for pipeline aggregation (M2-T10, m2-design §2.6) -- the attribution honesty backbone
that the ``GET /brands/{id}/pipeline`` endpoint (ui-spec §3.6/§6) calls.

Hermetic: in-memory SQLite, no network. The ``seeded_full_attribution`` fixture spans all four
mechanisms (direct / citation-linked / assisted / holdout) for tenant ``t1`` + brand ``b1`` and is
handed to ``pipeline_view`` as a *raw* ``Session`` -- ``pipeline_view`` scopes it to the requested
``tenant_id`` internally (that is what lets the same fixture answer for ``t1`` *and* prove ``t2``
sees nothing, TRD §7).

Beyond the two spec tests, the extra cases lock the honesty-critical invariants: dedup per lead
(a lead with several links is counted once), strongest-method bucketing (``direct`` >
``citation_linked`` > ``assisted``, disjoint buckets summing to ``influenced``), the
``attributed <= influenced`` guarantee, and ``top_answers`` grouping by prompt.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as SASession

from gw_geo.attribution.pipeline import pipeline_view
from gw_geo.common.db import (
    AttributionLink,
    Base,
    Brand,
    HoldoutCohort,
    Lead,
    Prompt,
    Session,
    Tenant,
)

_WINDOW_TS = datetime(2026, 6, 15, tzinfo=UTC)
_SINCE, _UNTIL = "2026-06-01", "2026-07-02"


def _session(sid: str, *, utm: dict[str, str] | None = None, engine: str | None = None) -> Session:
    return Session(
        id=sid,
        tenant_id="t1",
        brand_id="b1",
        visitor_id=f"v-{sid}",
        landing_url=f"https://acme.com/{sid}",
        referrer=None,
        utm=utm or {},
        engine=engine,
        ts=_WINDOW_TS,
    )


def _lead(lid: str, session_id: str | None, value: float | None) -> Lead:
    return Lead(
        id=lid,
        tenant_id="t1",
        brand_id="b1",
        visitor_id=f"v-{session_id or lid}",
        session_id=session_id,
        value_usd=value,
        ts=_WINDOW_TS,
    )


def _link(
    lid: str,
    *,
    method: str,
    confidence: str,
    engine: str,
    lead_id: str | None = None,
    session_id: str | None = None,
    prompt_id: str | None = None,
    value: float | None = None,
) -> AttributionLink:
    return AttributionLink(
        id=lid,
        tenant_id="t1",
        brand_id="b1",
        lead_id=lead_id,
        session_id=session_id,
        citation_id=None,
        prompt_id=prompt_id,
        engine=engine,
        method=method,
        confidence=confidence,
        value_usd=value,
        ts=_WINDOW_TS,
    )


@pytest.fixture
def seeded_full_attribution() -> SASession:
    """A raw SQLite session seeded with all four attribution mechanisms for t1/b1.

    Leads (touched by >=1 mechanism), with the strongest method that should own each in the
    breakdown:

    * ``l1`` $100 -- ``direct`` link (on session ``s1``) **and** a ``citation_linked`` link (prompt
      ``pa``, also on ``s1``): dedup must count it once and bucket it under ``direct`` (strongest),
      while ``top_answers`` still credits prompt ``pa`` for driving the visit.
    * ``l2`` $200 -- only a ``citation_linked`` link (prompt ``pb``, on session ``s2``) -> bucket
      ``citation_linked``.
    * ``l3`` $50  -- only an ``assisted`` link -> bucket ``assisted``.
    * ``l4`` $80  -- ``direct`` **and** ``assisted`` links -> bucket ``direct`` (strongest).
    * ``l5`` $999 -- **no** links at all -> untouched, excluded from every figure.

    So: leads=4, influenced=430, direct=180, citation_linked=200, assisted=50 (sum=430),
    attributed=380, top_answers={pb:$200/1, pa:$100/1}.

    Plus a holdout cohort ``ho1`` (is_holdout=True, prompt ``p-hold``) whose tagged sessions
    convert less than the untagged/optimized remainder, so ``measure_incrementality`` yields a
    positive lift and therefore a positive ``holdout_incremental``.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)

    raw.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
    raw.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=[]))
    raw.add(Prompt(id="pa", tenant_id="t1", brand_id="b1", text="best CRM for SaaS startups"))
    raw.add(Prompt(id="pb", tenant_id="t1", brand_id="b1", text="HubSpot alternatives"))

    # --- the four fuzzy-link mechanisms (direct / citation_linked / assisted) ---
    for sid, engine_name in (("s1", "perplexity"), ("s2", None), ("s3", None), ("s4", "chatgpt"),
                             ("s5", None)):
        raw.add(_session(sid, engine=engine_name))
    raw.add(_lead("l1", "s1", 100.0))
    raw.add(_lead("l2", "s2", 200.0))
    raw.add(_lead("l3", "s3", 50.0))
    raw.add(_lead("l4", "s4", 80.0))
    raw.add(_lead("l5", "s5", 999.0))

    raw.add(_link("lk-d1", method="direct", confidence="high", engine="perplexity",
                  lead_id="l1", session_id="s1", value=100.0))
    raw.add(_link("lk-c1", method="citation_linked", confidence="high", engine="perplexity",
                  session_id="s1", prompt_id="pa"))
    raw.add(_link("lk-c2", method="citation_linked", confidence="high", engine="perplexity",
                  session_id="s2", prompt_id="pb"))
    raw.add(_link("lk-a3", method="assisted", confidence="reported", engine="chatgpt",
                  lead_id="l3", session_id="s3", value=50.0))
    raw.add(_link("lk-d4", method="direct", confidence="high", engine="chatgpt",
                  lead_id="l4", session_id="s4", value=80.0))
    raw.add(_link("lk-a4", method="assisted", confidence="modeled", engine="aggregate",
                  lead_id="l4", session_id="s4", value=40.0))

    # --- holdout mechanism: tagged (holdout) side converts worse than the remainder ---
    raw.add(HoldoutCohort(id="ho1", tenant_id="t1", brand_id="b1", name="Q3 holdout",
                          kind="prompt", prompt_ids=["p-hold"], is_holdout=True))
    for i in range(4):
        sid = f"hold-s{i}"
        raw.add(_session(sid, utm={"prompt_id": "p-hold"}))
        if i < 1:  # 1/4 convert on the un-optimized holdout side
            raw.add(_lead(f"hold-l{i}", sid, 500.0))
    for i in range(4):
        sid = f"opt-s{i}"
        raw.add(_session(sid, utm={"prompt_id": "p-opt"}))
        if i < 3:  # 3/4 convert on the optimized side
            raw.add(_lead(f"opt-l{i}", sid, 500.0))

    raw.commit()
    return raw


# --- the two spec tests (M2-T10 task) ----------------------------------------------------------


def test_pipeline_shape_and_breakdown(seeded_full_attribution: SASession) -> None:
    # fixture seeds sessions/leads/links across all 4 methods + a holdout cohort for t1/b1
    out = pipeline_view(seeded_full_attribution, tenant_id="t1", brand_id="b1",
                        since="2026-06-01", until="2026-07-02")
    assert set(out) >= {"influenced", "attributed", "leads", "lift",
                        "top_answers", "method_breakdown", "confidence_note"}
    mb = out["method_breakdown"]
    assert set(mb) == {"direct", "citation_linked", "assisted", "holdout_incremental"}
    # attributed is the defensible subset -> <= influenced
    assert out["attributed"] <= out["influenced"]
    assert out["confidence_note"]  # never empty
    assert isinstance(out["top_answers"], list)


def test_tenant_isolation(seeded_full_attribution: SASession) -> None:
    other = pipeline_view(seeded_full_attribution, tenant_id="t2", brand_id="b1",
                          since="2026-06-01", until="2026-07-02")
    assert other["leads"] == 0  # t2 sees nothing of t1


# --- invariant-locking tests (dedup / strongest-method / attributed<=influenced / top_answers) -


def test_dedup_and_strongest_method_buckets(seeded_full_attribution: SASession) -> None:
    """Each lead counted once, bucketed under its strongest method; buckets are disjoint and sum
    to ``influenced`` (the untouched $999 lead is excluded entirely)."""
    out = pipeline_view(seeded_full_attribution, tenant_id="t1", brand_id="b1",
                        since=_SINCE, until=_UNTIL)
    mb = out["method_breakdown"]
    assert out["leads"] == 4  # l1..l4 touched; l5 untouched -> excluded
    assert out["influenced"] == pytest.approx(430.0)
    assert mb["direct"] == pytest.approx(180.0)  # l1 ($100, direct beats citation) + l4 ($80)
    assert mb["citation_linked"] == pytest.approx(200.0)  # l2
    assert mb["assisted"] == pytest.approx(50.0)  # l3
    # disjoint buckets sum exactly to influenced
    assert mb["direct"] + mb["citation_linked"] + mb["assisted"] == pytest.approx(
        out["influenced"]
    )


def test_attributed_is_defensible_subset(seeded_full_attribution: SASession) -> None:
    """``attributed`` = direct + citation_linked, and never exceeds ``influenced``."""
    out = pipeline_view(seeded_full_attribution, tenant_id="t1", brand_id="b1",
                        since=_SINCE, until=_UNTIL)
    assert out["attributed"] == pytest.approx(380.0)  # 180 + 200
    assert out["attributed"] <= out["influenced"]


def test_top_answers_grouped_by_prompt(seeded_full_attribution: SASession) -> None:
    """``top_answers`` groups citation_linked-driven leads by prompt (resolved to prompt text),
    sorted by value descending."""
    out = pipeline_view(seeded_full_attribution, tenant_id="t1", brand_id="b1",
                        since=_SINCE, until=_UNTIL)
    answers = out["top_answers"]
    assert [a["prompt"] for a in answers] == ["HubSpot alternatives", "best CRM for SaaS startups"]
    assert answers[0] == {"prompt": "HubSpot alternatives", "leads": 1, "value": pytest.approx(200.0)}
    assert answers[1]["leads"] == 1 and answers[1]["value"] == pytest.approx(100.0)


def test_holdout_lift_is_causal_and_incremental_dollars_positive(
    seeded_full_attribution: SASession,
) -> None:
    """The holdout cohort converts worse than the optimized remainder, so lift > 0 and the implied
    ``holdout_incremental`` is positive but capped below ``influenced``."""
    out = pipeline_view(seeded_full_attribution, tenant_id="t1", brand_id="b1",
                        since=_SINCE, until=_UNTIL)
    assert out["lift"] > 0.0
    incr = out["method_breakdown"]["holdout_incremental"]
    assert 0.0 < incr < out["influenced"]


def test_no_holdout_cohort_yields_zero_lift(seeded_full_attribution: SASession) -> None:
    """A brand with no holdout cohort reports ``lift == 0.0`` and ``holdout_incremental == 0.0``
    rather than raising."""
    out = pipeline_view(seeded_full_attribution, tenant_id="t1", brand_id="b-none",
                        since=_SINCE, until=_UNTIL)
    assert out["lift"] == 0.0
    assert out["method_breakdown"]["holdout_incremental"] == 0.0


def test_confidence_note_states_causal_caveat(seeded_full_attribution: SASession) -> None:
    """PRD §13 / m2-design §1: the note must name holdout as the only causal figure and flag the
    rest as correlational / low-confidence."""
    note = pipeline_view(seeded_full_attribution, tenant_id="t1", brand_id="b1",
                         since=_SINCE, until=_UNTIL)["confidence_note"].lower()
    assert "holdout" in note
    assert "causal" in note
    assert "correlational" in note
    assert "assisted" in note


def test_assisted_strongest_lead_credits_persisted_weighted_value() -> None:
    """PRD §13 anti-overclaim (review fix #1): an assisted-strongest lead contributes only the
    probability-weighted value the assisted writer persisted (``lead.value_usd * min(r, 1.0)``),
    never its full lead value -- otherwise a low-confidence modelled lift is silently booked at
    100c on the dollar.

    A $1000 lead credited by assisted modelling at ``r = 0.3`` (so ``assisted.py`` persisted a
    ``$300`` link) must show ``$300`` in both the assisted bucket and ``influenced`` -- not
    ``$1000`` -- and nothing in ``attributed`` (assisted is not a defensible method).
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    raw = SASession(engine)
    raw.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
    raw.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=[]))
    # A $1000 lead on a branded/direct (engine=None) session, credited by assisted modelling. The
    # assisted writer persisted value_usd = 1000 * min(0.3, 1.0) = 300 on the link.
    raw.add(_session("s1", engine=None))
    raw.add(_lead("l1", "s1", 1000.0))
    raw.add(_link("lk-a1", method="assisted", confidence="modeled", engine="aggregate",
                  lead_id="l1", session_id="s1", value=300.0))
    raw.commit()

    out = pipeline_view(raw, tenant_id="t1", brand_id="b1", since=_SINCE, until=_UNTIL)
    assert out["leads"] == 1
    assert out["method_breakdown"]["assisted"] == pytest.approx(300.0)  # weighted, NOT 1000
    assert out["influenced"] == pytest.approx(300.0)  # reflects the weighted credit, not 1000
    assert out["attributed"] == pytest.approx(0.0)  # assisted is never defensible
    assert out["attributed"] <= out["influenced"]
