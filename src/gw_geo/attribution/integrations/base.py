"""`Integration` protocol (m2-design §5, docs/tasks/M2-T11): one shared interface for every
pluggable third-party connector -- HubSpot/Salesforce here (`crm.py`), GA4 next (T12's `ga4.py`).

Kept intentionally minimal and stable: T12 imports this Protocol directly, so any change here
ripples to every connector. A connector is not required to subclass this -- like
`measurement/probe/base.py`'s `EngineAdapter`, conformance is structural (duck-typed); this module
exists to pin the shape down in one place and give it a name to type against.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from gw_geo.common.db import TenantScopedSession


@runtime_checkable
class Integration(Protocol):
    """A third-party connector: `connect` persists connection state, `sync` pulls upstream data.

    Both methods are tenant-scoped via `TenantScopedSession` (TRD §7) -- neither may read or
    write another tenant's rows.
    """

    kind: str  # e.g. "hubspot" | "salesforce" | "ga4"

    def connect(
        self, session: TenantScopedSession, *, tenant_id: str, config: dict[str, Any]
    ) -> dict[str, str]:
        """Persist this connector's connection state for `tenant_id` in the `integration` table.

        `config` carries connector-specific setup (e.g. a secret-store reference under a key like
        `"access_token_ref"`). Implementations must never persist a raw credential -- only a
        pointer to where one lives -- so a caller accidentally including a raw secret in `config`
        must not cause it to reach the database. Returns `{"status": "connected" | "pending"}`.
        """
        ...

    async def sync(self, session: TenantScopedSession, *, tenant_id: str, brand_id: str) -> int:
        """Pull upstream data and enrich this brand's local rows; return the count enriched.

        For a CRM connector that means matching upstream deals to `lead` rows by contact email and
        writing `lead.crm_stage` / `lead.value_usd`; the return value is the number of `lead` rows
        touched.
        """
        ...
