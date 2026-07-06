"""DB-backed :class:`~gw_geo.api.routers.opportunities.OpportunityService` (ui-spec §3.4/§6).

The stateful counterpart to :func:`gw_geo.orchestration.opportunities.build_opportunities` (the pure
gap-ranking function): this reads the already-persisted `Opportunity` rows the ranking pipeline
writes, and drives the ``act`` -> content flow ("Fix this ▸") by spawning a pre-scoped draft through
the content pipeline (T22).

Both operations are **tenant-scoped**: every query filters on the caller's own ``tenant_id`` (from
the token, never the client), so an ``Opportunity`` id belonging to another tenant is indistinguish-
able from one that doesn't exist -- a :class:`LookupError` (-> **404**), never a cross-tenant leak,
matching ``routers/brands.py``'s ownership posture.

``act`` composes onto the injected :class:`~gw_geo.content.pipeline.ContentService`: it hydrates the
opportunity's brand, grounds a prompt derived from the gap against that brand's KB, generates a
draft, marks the opportunity ``acted``, and returns the new ``content_id`` -- so the spawned draft
flows through the *same* honesty gate (guardrails + human approval) every other draft does. What is
**not** built here (a residual stub, see CONCERNS): the population of `Opportunity` rows themselves
(running ``build_opportunities`` over measurement/ranking snapshots and persisting them) is a
separate worker; this service surfaces + acts on rows that pipeline has already written.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as SASession

from gw_geo.common.db import Brand as BrandRow
from gw_geo.common.db import Opportunity as OpportunityRow
from gw_geo.common.models import Brand
from gw_geo.content.pipeline import ContentService


class DbOpportunityService:
    """Reads persisted `Opportunity` rows and spawns content via the injected `ContentService`.

    Bound to one `tenant_id`; the `content_service` is expected to be scoped to the same tenant
    (its DB-backed store and per-brand KB share this request's session/settings).
    """

    def __init__(
        self, *, session: SASession, tenant_id: str, content_service: ContentService
    ) -> None:
        self._session = session
        self._tenant_id = tenant_id
        self._content = content_service

    def list_for_brand(self, *, tenant_id: str, brand_id: str) -> list[dict[str, Any]]:
        """This tenant's persisted opportunities for `brand_id`, ranked by `est_impact` desc.

        Returns the ui-spec §6 row shape ``{id, title, rationale, est_impact, engine}`` -- the
        underlying `Opportunity`'s `tenant_id`/`brand_id`/`source_gap`/`status` stay server-side.
        """
        rows = (
            self._session.query(OpportunityRow)
            .filter(
                OpportunityRow.tenant_id == tenant_id,
                OpportunityRow.brand_id == brand_id,
            )
            .order_by(OpportunityRow.est_impact.desc())
            .all()
        )
        return [
            {
                "id": row.id,
                "title": row.title,
                "rationale": row.rationale,
                "est_impact": row.est_impact,
                "engine": row.engine,
            }
            for row in rows
        ]

    def act(self, *, tenant_id: str, opportunity_id: str) -> dict[str, Any]:
        """Spawn a pre-scoped content draft from `opportunity_id`; return ``{"content_id": ...}``.

        Resolves the opportunity within `tenant_id` (an unknown id, *or* one owned by another
        tenant -> :class:`LookupError` / 404), hydrates its brand, derives a generation prompt from
        the gap, grounds it against the brand's KB, and generates a draft through the content
        pipeline (which runs the guardrails -- the draft still needs human approval before publish).
        Marks the opportunity ``acted`` so it drops out of the open queue.
        """
        opp = (
            self._session.query(OpportunityRow)
            .filter(
                OpportunityRow.id == opportunity_id,
                OpportunityRow.tenant_id == tenant_id,
            )
            .one_or_none()
        )
        if opp is None:
            raise LookupError(f"opportunity {opportunity_id!r} not found")
        brand_row = (
            self._session.query(BrandRow)
            .filter(BrandRow.id == opp.brand_id, BrandRow.tenant_id == tenant_id)
            .one_or_none()
        )
        if brand_row is None:
            # The opportunity references a brand this tenant no longer owns -- same 404 as above.
            raise LookupError(f"opportunity {opportunity_id!r} not found")

        brand = Brand(
            id=brand_row.id,
            tenant_id=tenant_id,
            name=brand_row.name,
            domain=brand_row.domain,
            competitors=list(brand_row.competitors),
        )
        # Derive the generation prompt from the gap the opportunity describes. A richer mapping
        # (tying the draft to a specific target Prompt row) is a follow-on -- see CONCERNS.
        prompt_text = f"{opp.title}. {opp.rationale}"
        facts = self._content.ground(brand_id=brand.id, prompt_text=prompt_text)
        draft, _ = self._content.generate(
            brand=brand,
            prompt_text=prompt_text,
            facts=facts,
            feature_profile=None,
            target_engine=opp.engine,
        )
        opp.status = "acted"
        self._session.commit()
        return {"content_id": draft.id}


__all__ = ["DbOpportunityService"]
