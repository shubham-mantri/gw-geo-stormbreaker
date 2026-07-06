"""End-to-end LOCAL attribution flow (W4): install snippet -> AI-referred pixel beacons -> lead ->
reconcile batch -> pipeline reflects the attributed value.

Hermetic: the shared in-memory SQLite ``engine`` + ``app_client`` fixtures (``tests/api/conftest.py``),
no CDN, no network. Exercises the real W4 wiring end to end -- the ``GET /lead-capture/snippet`` tag
now points at the LOCAL pixel, its minted write-key authorizes the ``POST /lead-capture/collect``
beacons, and the ``reconcile_attribution`` batch turns the captured session+lead into an
``attribution_link`` the pipeline surfaces. Real-Postgres FK-safety is covered by the runnable
scratch script (SQLite defaults FK enforcement off).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session as SASession

from gw_geo.api.routers import leadcapture
from gw_geo.attribution.trigger import reconcile_attribution
from gw_geo.common.config import Settings
from gw_geo.common.db import AttributionLink, Brand, Tenant


def _window() -> tuple[str, str]:
    """A trailing 90-day inclusive window ending today (covers the "now" beacon timestamps)."""
    until = datetime.now(timezone.utc).date()
    since = until - timedelta(days=89)
    return since.isoformat(), until.isoformat()


def test_local_flow_snippet_to_pipeline(
    app_client: TestClient, engine: Engine, admin_token: str, settings: Settings
) -> None:
    auth = {"Authorization": f"Bearer {admin_token}"}

    # Seed tenant t1 + brand b1 (committed FK parents) -- admin_token is scoped to t1.
    with SASession(engine) as s:
        s.add(Tenant(id="t1", name="Acme", sampling_budget_daily=100.0))
        s.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com", competitors=[]))
        s.commit()

    # Align the public collect endpoint's write-key salt with the snippet's (app settings) salt, so
    # the key the snippet mints is the same one collect resolves -- the exact W4 snippet->pixel path.
    app_client.app.dependency_overrides[leadcapture.get_pixel_salt] = (
        lambda: settings.pixel_write_key_salt
    )

    # 1) Fetch the install snippet: it must point at the LOCAL pixel, never a CDN, and carry the
    #    local collect origin as data-api.
    r = app_client.get("/lead-capture/snippet?brand_id=b1", headers=auth)
    assert r.status_code == 200
    snippet = r.json()["snippet"]
    assert "cdn.gwgeo.io" not in snippet
    assert settings.pixel_url in snippet  # local /pixel/gwgeo.js URL
    assert f'data-api="{settings.pixel_api_base}"' in snippet
    match = re.search(r'data-key="([^"]+)"', snippet)
    assert match is not None
    write_key = match.group(1)

    # 2) The pixel beacons an AI-engine-referred (perplexity) session, then a lead on the same
    #    visitor with an email + value_usd -- exactly what gwgeo.js posts.
    r = app_client.post(
        "/lead-capture/collect",
        json={
            "write_key": write_key,
            "type": "session",
            "visitor_id": "v-1",
            "landing_url": "https://acme.com/crm",
            "referrer": "https://www.perplexity.ai/",
            "utm": {"utm_source": "perplexity"},
        },
    )
    assert r.status_code == 202
    r = app_client.post(
        "/lead-capture/collect",
        json={
            "write_key": write_key,
            "type": "lead",
            "visitor_id": "v-1",
            "email": "buyer@x.com",
            "value_usd": 750.0,
        },
    )
    assert r.status_code == 202

    # Before reconcile: the lead is captured but unattributed (no attribution_link exists yet).
    since, until = _window()
    r = app_client.get("/brands/b1/pipeline?range=90d", headers=auth)
    assert r.status_code == 200
    assert r.json()["attributed"] == 0.0

    # 3) Run the reconcile batch on the shared engine (the endpoint's BackgroundTask opens its own
    #    session on settings.database_url; here we drive the same unit directly against the test DB).
    with SASession(engine) as s:
        counts = reconcile_attribution(
            session=s, tenant_id="t1", brand_id="b1", since=since, until=until
        )
    assert counts["direct"] == 1

    # 4) An attribution_link now exists, and the pipeline reflects the attributed value.
    with SASession(engine) as s:
        links = s.query(AttributionLink).all()
        assert len(links) == 1
        assert links[0].method == "direct" and links[0].engine == "perplexity"

    r = app_client.get("/brands/b1/pipeline?range=90d", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["influenced"] == 750.0
    assert body["attributed"] == 750.0
    assert body["leads"] == 1
    assert body["method_breakdown"]["direct"] == 750.0
