"""GA4 referral-reconciliation connector (m2-design §5, docs/tasks/M2-T12).

Implements the `Integration` protocol (`base.py`) a third time, alongside `crm.py`'s HubSpot/
Salesforce connectors: `connect` persists connection state into the `integration` table, `sync`
pulls AI-referral channel sessions from the GA4 Data API.

**Reconciliation only.** Unlike the CRM connectors -- which *enrich* `lead` rows -- GA4 here never
mutates a `lead` (or any other business row): the lead-capture pixel remains the system of record
for attribution (m2-design §5, TRD §6). `sync` only *compares* GA4's own session counts per AI
engine against the pixel's own counts (`Session.engine`, populated by mechanism 1,
`attribution/referral.py`'s `link_direct`) and returns how many engines GA4 saw this pull. There is
deliberately no code path in this module that reads or writes `Lead` at all, so that invariant
holds by construction rather than by convention.

Every HTTP call is a plain `httpx` request against an injectable `httpx.AsyncClient` -- no vendor
SDK, consistent with `crm.py` and every M0/M1 engine adapter -- so the default test suite never
touches the network (`respx`-mocked in `tests/attribution/integrations/test_ga4.py`).

**Source -> engine mapping.** The GA4 Data API's `sessionSource` dimension reports a bare referrer
host (e.g. `"perplexity.ai"`), exactly the shape `attribution/referral.py`'s `AI_ENGINE_REFERRERS`
map already keys on (T06) -- reused here verbatim rather than duplicated, so the two mechanisms
(pixel referrer classification and GA4 corroboration) can never independently drift on which hosts
count as AI engines. A GA4 row whose source isn't in that map (organic search, direct, paid, etc.)
is not AI-referred traffic and is dropped. Two rows can map to the same engine (e.g. `"chatgpt.com"`
and `"chat.openai.com"`); their session counts are summed.

**Secrets.** As with `crm.py`'s bearer tokens, `sync`'s GA4 credential material is read straight
from `Settings.ga4_credentials_ref` -- populated from the environment (ultimately SSM-backed at
deploy time, TRD §7), never hardcoded here. `connect` is a separate, secret-free concern: it only
ever extracts a `credentials_ref` *pointer* out of the caller-supplied `config` dict and persists
that pointer (plus a status) into the `integration` table; it never inspects, let alone stores, a
raw credential. Resolving a ref pointer into live per-tenant OAuth material at sync time is the
same `SecretProvider` gap `crm.py`/`common/wiring.py` already flag as not yet implemented here.

**Lookback window.** GA4's Data API takes a date range per request; this connector has no
`since`/`until` parameter (unlike `referral.py`'s `link_direct`), so `sync` asks for a rolling
7-day window via GA4's own relative-date syntax (`"7daysAgo"`..`"today"`) -- long enough to absorb
GA4's well-known reporting-latency lag (sessions can take 24-48h to fully materialize) while still
being a cheap, frequent-enough reconciliation cadence.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from gw_geo.attribution.referral import AI_ENGINE_REFERRERS
from gw_geo.common.config import Settings
from gw_geo.common.db import Integration, Session, TenantScopedSession

logger = logging.getLogger(__name__)

_RUNREPORT_URL_TEMPLATE = "https://analyticsdata.googleapis.com/v1beta/{property_id}:runReport"
_LOOKBACK_START = "7daysAgo"  # GA4 relative-date syntax; see module docstring re: reporting latency
_LOOKBACK_END = "today"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_same_tenant(session: TenantScopedSession, tenant_id: str) -> None:
    """Raise `ValueError` if `session` is not scoped to `tenant_id` (TRD §7 fail-closed guard)."""
    if session.tenant_id != tenant_id:
        raise ValueError(f"session is scoped to tenant_id={session.tenant_id!r}, not {tenant_id!r}")


def _extract_ai_engine_sessions(payload: dict[str, Any]) -> dict[str, int]:
    """Sum GA4 `runReport` session counts per AI engine, from rows keyed by a source dimension.

    Each `rows[i]` pairs one dimension value (GA4's `sessionSource`, e.g. `"perplexity.ai"`) with
    one metric value (a session count, a numeric string per the Data API's wire format). Rows whose
    source doesn't match a known AI-engine referrer host (`AI_ENGINE_REFERRERS`, T06) are organic/
    other-channel traffic and are dropped -- this connector only ever reconciles AI-referred
    sessions. Malformed rows (missing either value) are skipped rather than raising.
    """
    counts: dict[str, int] = {}
    for row in payload.get("rows", []):
        dimension_values = row.get("dimensionValues") or []
        metric_values = row.get("metricValues") or []
        if not dimension_values or not metric_values:
            continue
        source = str(dimension_values[0].get("value", "")).strip().lower()
        engine = AI_ENGINE_REFERRERS.get(source)
        if engine is None:
            continue
        counts[engine] = counts.get(engine, 0) + int(metric_values[0].get("value", 0))
    return counts


def _pixel_engine_counts(session: TenantScopedSession, *, brand_id: str) -> dict[str, int]:
    """Tenant-scoped count of pixel-recorded `session` rows per classified engine, for `brand_id`.

    Mirrors `_extract_ai_engine_sessions`'s shape so both feed `reconcile` symmetrically. Reads
    `Session.engine` (mechanism 1, `attribution/referral.py`'s `link_direct`) -- the pixel's own
    view of which AI engine referred each session -- so this connector never has to re-derive
    engine classification itself.
    """
    rows = (
        session.query(Session)
        .filter(Session.brand_id == brand_id, Session.engine.isnot(None))
        .all()
    )
    counts: dict[str, int] = {}
    for row in rows:
        if row.engine is None:  # pragma: no cover -- excluded by the `isnot(None)` filter above
            continue
        counts[row.engine] = counts.get(row.engine, 0) + 1
    return counts


def reconcile(pixel_counts: dict[str, int], ga4_counts: dict[str, int]) -> dict[str, dict[str, int]]:
    """Per-engine pixel-vs-GA4 comparison: `{engine: {"pixel": n, "ga4": m, "delta": m - n}}`.

    GA4 is reconciliation only (m2-design §5) -- the pixel remains system of record; `delta`
    (GA4 minus pixel) surfaces where GA4 saw more or fewer AI-referred sessions than the pixel
    recorded (e.g. a positive delta when ad/tracking blockers suppress the first-party pixel beacon
    but GA4 still sees the session). Covers the union of engines named by either side, so an engine
    tracked by only one source still gets a full record with the other side defaulted to 0.
    """
    engines = set(pixel_counts) | set(ga4_counts)
    return {
        engine: {
            "pixel": pixel_counts.get(engine, 0),
            "ga4": ga4_counts.get(engine, 0),
            "delta": ga4_counts.get(engine, 0) - pixel_counts.get(engine, 0),
        }
        for engine in engines
    }


class GA4Integration:
    """`Integration` for GA4: Data API session counts reconciled against the pixel, per AI engine."""

    kind = "ga4"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client if client is not None else httpx.AsyncClient()

    def connect(
        self, session: TenantScopedSession, *, tenant_id: str, config: dict[str, Any]
    ) -> dict[str, str]:
        """Persist (upsert) this tenant's `ga4` row in the `integration` table.

        Only ever reads the `credentials_ref` key out of `config`; any other key a caller passes
        (e.g. a raw credential under a different key, by mistake) is never inspected, so it cannot
        reach the database -- same contract as `crm.py`'s `_connect`.
        """
        _require_same_tenant(session, tenant_id)
        config_ref = config.get("credentials_ref")
        status = "connected" if config_ref else "pending"

        row = session.query(Integration).filter(Integration.kind == self.kind).first()
        if row is None:
            row = Integration(
                id=uuid4().hex,
                tenant_id=tenant_id,
                kind=self.kind,
                status=status,
                config_ref=config_ref,
                connected_at=_utcnow() if status == "connected" else None,
            )
            session.add(row)
        else:
            row.status = status
            row.config_ref = config_ref
            if status == "connected":
                row.connected_at = _utcnow()
        session.commit()
        return {"status": row.status}

    async def sync(self, session: TenantScopedSession, *, tenant_id: str, brand_id: str) -> int:
        """Pull AI-referral session counts from the GA4 Data API and reconcile against the pixel.

        Calls `runReport` for `sessionSource` x `sessions` over a rolling 7-day window (module
        docstring), extracts the AI-engine-attributable rows (`_extract_ai_engine_sessions`), and
        compares them against the pixel's own per-engine counts (`_pixel_engine_counts`) via
        `reconcile`. The resulting comparison is logged (there is no persisted "reconciliation"
        table yet -- out of scope for this task) for now; the pixel's `lead`/`session` rows are
        never written. Returns the number of distinct AI engines GA4's report attributed sessions
        to this pull -- 0 when the window had no AI-referred traffic.

        Raises `ValueError` if `session` is scoped to a different tenant than `tenant_id`
        (TRD §7 fail-closed guard, same convention as every other tenant-scoped mechanism).
        """
        _require_same_tenant(session, tenant_id)

        response = await self._client.post(
            _RUNREPORT_URL_TEMPLATE.format(property_id=self._settings.ga4_property_id),
            headers={"Authorization": f"Bearer {self._settings.ga4_credentials_ref}"},
            json={
                "dateRanges": [{"startDate": _LOOKBACK_START, "endDate": _LOOKBACK_END}],
                "dimensions": [{"name": "sessionSource"}],
                "metrics": [{"name": "sessions"}],
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

        ga4_counts = _extract_ai_engine_sessions(payload)
        pixel_counts = _pixel_engine_counts(session, brand_id=brand_id)
        reconciliation = reconcile(pixel_counts, ga4_counts)
        logger.info(
            "ga4 reconciliation tenant_id=%s brand_id=%s: %s", tenant_id, brand_id, reconciliation
        )
        return len(ga4_counts)
