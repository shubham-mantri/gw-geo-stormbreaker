"""Contract-fidelity gate (M2-T21): every M2 endpoint's real response validates against the
ui-spec §6 JSON schema (``schemas_uispec.py``).

The ui-spec is the single source of truth the ``web/`` TypeScript types mirror; this test pins the
*backend* to it, so a response-shape drift (a renamed/added/dropped field, a wrong type) fails here
rather than silently reaching the dashboard as an ``undefined`` at runtime. Runs against the real
``create_app`` over seeded in-memory SQLite via the shared ``tests/api/conftest.py`` fixtures (no
live services), driving each endpoint exactly as the dashboard does -- bearer-authed, tenant ``t1``,
brand ``b1``.

Every schema is ``additionalProperties: false``: the assertion is fidelity, not mere "has the keys
the UI reads".
"""

from __future__ import annotations

import jsonschema
from fastapi.testclient import TestClient

from tests.api.schemas_uispec import (
    ALERTS_SCHEMA,
    BRANDS_SCHEMA,
    OVERVIEW_SCHEMA,
    PIPELINE_SCHEMA,
    PROMPTS_SCHEMA,
    SNIPPET_SCHEMA,
    SOURCES_SCHEMA,
    VISIBILITY_SCHEMA,
)


def _get(client: TestClient, path: str, token: str) -> object:
    """GET ``path`` as tenant ``t1``; assert 200 and return the decoded JSON body."""
    resp = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text}"
    return resp.json()


def test_brands_matches_uispec(
    app_client: TestClient, t1_token: str, seeded_brands: None
) -> None:
    jsonschema.validate(_get(app_client, "/brands", t1_token), BRANDS_SCHEMA)


def test_overview_matches_uispec(
    app_client: TestClient, t1_token: str, seeded_snapshots: None
) -> None:
    body = _get(app_client, "/brands/b1/overview?range=30d", t1_token)
    jsonschema.validate(body, OVERVIEW_SCHEMA)


def test_visibility_matches_uispec(
    app_client: TestClient, t1_token: str, seeded_snapshots: None
) -> None:
    body = _get(app_client, "/brands/b1/visibility?range=30d", t1_token)
    jsonschema.validate(body, VISIBILITY_SCHEMA)


def test_sources_matches_uispec(
    app_client: TestClient, t1_token: str, seeded_citations: None
) -> None:
    body = _get(app_client, "/brands/b1/sources?range=30d", t1_token)
    jsonschema.validate(body, SOURCES_SCHEMA)


def test_pipeline_matches_uispec(
    app_client: TestClient, t1_token: str, seeded_full_attribution: None
) -> None:
    # includes method_breakdown + confidence_note (the anti-overclaim contract, PRD §13).
    body = _get(app_client, "/brands/b1/pipeline?range=90d", t1_token)
    jsonschema.validate(body, PIPELINE_SCHEMA)


def test_alerts_matches_uispec(
    app_client: TestClient, t1_token: str, seeded_drift: None
) -> None:
    jsonschema.validate(_get(app_client, "/brands/b1/alerts", t1_token), ALERTS_SCHEMA)


def test_prompts_matches_uispec(
    app_client: TestClient, t1_token: str, seeded_full_attribution: None
) -> None:
    # seeded_full_attribution seeds a prompt (`p-cta`) for b1, so the list is non-empty.
    jsonschema.validate(_get(app_client, "/brands/b1/prompts", t1_token), PROMPTS_SCHEMA)


def test_snippet_matches_uispec(
    app_client: TestClient, t1_token: str, seeded_brands: None
) -> None:
    # The dashboard's fixed client sends the brand_id query param (M2-T21 lib/api.ts fix); mirror it.
    body = _get(app_client, "/lead-capture/snippet?brand_id=b1", t1_token)
    jsonschema.validate(body, SNIPPET_SCHEMA)
