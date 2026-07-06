"""Guards the real, per-request content/opportunities providers `create_app` installs.

The hermetic router tests always *override* `get_content_service` / `get_kb_factory` /
`get_opportunity_service` with fakes, so the real wiring (`api/wiring.py`) is otherwise never
exercised. These two tests pin (a) that `create_app` actually points the routers' raising defaults
at the real providers, and (b) that building a real `ContentService` from default `Settings` is
I/O-free (the gateway clients + vector store connect lazily), so app/handler construction never
opens a connection -- the same guarantee `test_app.test_mangum_handler_importable` relies on.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session as SASession

from gw_geo.api import wiring
from gw_geo.api.app import create_app
from gw_geo.api.auth import Principal
from gw_geo.api.routers import content, opportunities
from gw_geo.common.config import Settings
from gw_geo.content.pipeline import ContentService
from gw_geo.content.publish import base
from gw_geo.orchestration.opportunity_service import DbOpportunityService


def test_create_app_installs_real_providers(settings: Settings) -> None:
    app = create_app(settings)
    overrides = app.dependency_overrides
    assert overrides[content.get_content_service] is wiring.content_service_provider
    assert overrides[content.get_kb_factory] is wiring.kb_factory_provider
    assert overrides[opportunities.get_opportunity_service] is wiring.opportunity_service_provider


def test_real_services_construct_without_io(settings: Settings, engine: Engine) -> None:
    # build_content_service populates the shared connector registry; clear it afterward so this
    # global side effect never leaks into the publish-registry tests.
    try:
        with SASession(engine) as session:
            svc = wiring.build_content_service(
                session=session, settings=settings, tenant_id="t1"
            )
            assert isinstance(svc, ContentService)
            opp_svc = wiring.opportunity_service_provider(
                session=session,
                settings=settings,
                principal=Principal(user_id="u1", tenant_id="t1", role="editor"),
            )
            assert isinstance(opp_svc, DbOpportunityService)
    finally:
        base.clear_registry()


def test_build_content_service_warns_originality_not_enforced(
    settings: Settings, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """M5 review (honesty): the LOCAL-only build wires no `CorpusSearch`, so originality is not
    enforced. `build_content_service` must SURFACE that -- a WARNING at build time and
    `originality_enforced=False` on the service -- so a plagiarized draft can never pass that leg
    *silently*. This does not change what blocks publish (approval + claim-grounding remain).
    """
    try:
        with (
            SASession(engine) as session,
            caplog.at_level(logging.WARNING, logger="gw_geo.api.wiring"),
        ):
            svc = wiring.build_content_service(
                session=session, settings=settings, tenant_id="t1"
            )
        assert svc._originality_enforced is False
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "expected a WARNING that originality is not enforced"
        assert "originality" in caplog.text.lower()
        assert "originality_enforced=False" in caplog.text
    finally:
        base.clear_registry()
