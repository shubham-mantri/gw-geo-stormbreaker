"""Tests for the brands + overview endpoints (M2-T13, ui-spec.md §6/§3.1) and the
``POST /brands/{id}/measure`` live-measurement trigger (W2 live wiring).

Fixtures (``app_client``, ``t1_token``, ``t2_token``, ``viewer_token``, ``editor_token``,
``seeded_brands``, ``seeded_snapshots``) live in ``tests/api/conftest.py``. Hermetic: in-memory
SQLite, no live calls -- the trigger tests patch ``run_measurement_job`` so the enqueued background
task never builds a real runtime or probes an engine.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient


def test_list_brands_scoped(app_client: TestClient, t1_token: str, seeded_brands: None) -> None:
    # t1 owns b1; t2 owns b2 -- a t1 token must only ever see b1 (tenant isolation).
    r = app_client.get("/brands", headers={"Authorization": f"Bearer {t1_token}"})
    assert r.status_code == 200
    ids = [b["id"] for b in r.json()]
    assert "b1" in ids and "b2" not in ids


def test_create_brand_requires_editor(app_client: TestClient, viewer_token: str) -> None:
    r = app_client.post(
        "/brands",
        json={"name": "Acme", "domain": "acme.com"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403


def test_overview_shape(app_client: TestClient, t1_token: str, seeded_snapshots: None) -> None:
    r = app_client.get(
        "/brands/b1/overview?range=30d", headers={"Authorization": f"Bearer {t1_token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"sov", "mention_rate", "pipeline", "leads", "trend"}
    assert isinstance(body["trend"], list)


def test_overview_foreign_brand_404(app_client: TestClient, t1_token: str) -> None:
    r = app_client.get("/brands/b2/overview", headers={"Authorization": f"Bearer {t1_token}"})
    assert r.status_code == 404


# --- POST /brands/{id}/measure (W2 live-measurement trigger) ---------------------------------


def test_measure_enqueues_job(
    app_client: TestClient, editor_token: str, seeded_brands: None
) -> None:
    # The TestClient runs the enqueued BackgroundTask before returning, so a patched
    # run_measurement_job records that it was scheduled -- with no live probe/DB/S3 work.
    with patch("gw_geo.api.routers.brands.run_measurement_job") as job:
        r = app_client.post(
            "/brands/b1/measure",
            json={"engines": ["perplexity"], "n_samples": 4},
            headers={"Authorization": f"Bearer {editor_token}"},
        )
    assert r.status_code == 202
    body = r.json()
    assert body["brand_id"] == "b1"
    assert body["engines"] == ["perplexity"]
    assert body["n_samples"] == 4
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "t1"  # from the token, never the client
    assert kwargs["brand_id"] == "b1"
    assert kwargs["engines"] == ["perplexity"]
    assert kwargs["n_samples"] == 4


def test_measure_defaults_when_body_omitted(
    app_client: TestClient, editor_token: str, seeded_brands: None
) -> None:
    # Test settings carry no engine API keys, so no engines are configured; n falls back to the
    # settings default (8). Verifies the endpoint resolves defaults without a request body.
    with patch("gw_geo.api.routers.brands.run_measurement_job") as job:
        r = app_client.post(
            "/brands/b1/measure", headers={"Authorization": f"Bearer {editor_token}"}
        )
    assert r.status_code == 202
    body = r.json()
    assert body["engines"] == []
    assert body["n_samples"] == 8
    job.assert_called_once()


def test_measure_requires_editor(
    app_client: TestClient, viewer_token: str, seeded_brands: None
) -> None:
    with patch("gw_geo.api.routers.brands.run_measurement_job") as job:
        r = app_client.post(
            "/brands/b1/measure",
            json={"engines": ["perplexity"]},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
    assert r.status_code == 403
    job.assert_not_called()


def test_measure_foreign_brand_404(
    app_client: TestClient, t1_token: str, seeded_brands: None
) -> None:
    # t1 requesting b2 (owned by t2): collapses to 404, never confirming b2 exists.
    with patch("gw_geo.api.routers.brands.run_measurement_job") as job:
        r = app_client.post(
            "/brands/b2/measure",
            json={"engines": ["perplexity"]},
            headers={"Authorization": f"Bearer {t1_token}"},
        )
    assert r.status_code == 404
    job.assert_not_called()


def test_measure_unknown_brand_404(
    app_client: TestClient, t1_token: str, seeded_brands: None
) -> None:
    with patch("gw_geo.api.routers.brands.run_measurement_job") as job:
        r = app_client.post(
            "/brands/does-not-exist/measure",
            json={"engines": ["perplexity"]},
            headers={"Authorization": f"Bearer {t1_token}"},
        )
    assert r.status_code == 404
    job.assert_not_called()


# --- POST /brands/{id}/opportunities/refresh (W3 opportunity-generation trigger) -------------


def test_refresh_opportunities_enqueues_job(
    app_client: TestClient, editor_token: str, seeded_brands: None
) -> None:
    # The TestClient runs the enqueued BackgroundTask before returning, so a patched
    # run_opportunity_refresh_job records that it was scheduled -- with no live ranking/DB work.
    with patch("gw_geo.api.routers.brands.run_opportunity_refresh_job") as job:
        r = app_client.post(
            "/brands/b1/opportunities/refresh",
            headers={"Authorization": f"Bearer {editor_token}"},
        )
    assert r.status_code == 202
    body = r.json()
    assert body == {"status": "accepted", "brand_id": "b1"}
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "t1"  # from the token, never the client
    assert kwargs["brand_id"] == "b1"


def test_refresh_opportunities_requires_editor(
    app_client: TestClient, viewer_token: str, seeded_brands: None
) -> None:
    with patch("gw_geo.api.routers.brands.run_opportunity_refresh_job") as job:
        r = app_client.post(
            "/brands/b1/opportunities/refresh",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
    assert r.status_code == 403  # RBAC gate (ui-spec §5): viewer cannot trigger generation
    job.assert_not_called()


def test_refresh_opportunities_foreign_brand_404(
    app_client: TestClient, t1_token: str, seeded_brands: None
) -> None:
    # t1 requesting b2 (owned by t2): collapses to 404, never confirming b2 exists.
    with patch("gw_geo.api.routers.brands.run_opportunity_refresh_job") as job:
        r = app_client.post(
            "/brands/b2/opportunities/refresh",
            headers={"Authorization": f"Bearer {t1_token}"},
        )
    assert r.status_code == 404
    job.assert_not_called()


# --- POST /brands/{id}/ranking/refresh (M5 candidate-sourcing ranking trigger) --------------


def test_refresh_ranking_enqueues_job(
    app_client: TestClient, editor_token: str, seeded_brands: None
) -> None:
    # The TestClient runs the enqueued BackgroundTask before returning, so a patched
    # run_ranking_refresh_job records that it was scheduled -- with no live crawl/train work.
    with patch("gw_geo.api.routers.brands.run_ranking_refresh_job") as job:
        r = app_client.post(
            "/brands/b1/ranking/refresh",
            json={"engines": ["perplexity", "openai"]},
            headers={"Authorization": f"Bearer {editor_token}"},
        )
    assert r.status_code == 202
    body = r.json()
    assert body == {"status": "accepted", "brand_id": "b1", "engines": ["perplexity", "openai"]}
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "t1"  # from the token, never the client
    assert kwargs["brand_id"] == "b1"
    assert kwargs["engines"] == ["perplexity", "openai"]


def test_refresh_ranking_defaults_engines_when_body_omitted(
    app_client: TestClient, editor_token: str, seeded_brands: None
) -> None:
    # Test settings carry no engine API keys, so no engines are configured -> resolves to [].
    with patch("gw_geo.api.routers.brands.run_ranking_refresh_job") as job:
        r = app_client.post(
            "/brands/b1/ranking/refresh", headers={"Authorization": f"Bearer {editor_token}"}
        )
    assert r.status_code == 202
    assert r.json()["engines"] == []
    job.assert_called_once()


def test_refresh_ranking_requires_editor(
    app_client: TestClient, viewer_token: str, seeded_brands: None
) -> None:
    with patch("gw_geo.api.routers.brands.run_ranking_refresh_job") as job:
        r = app_client.post(
            "/brands/b1/ranking/refresh",
            json={"engines": ["perplexity"]},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
    assert r.status_code == 403  # RBAC gate (ui-spec §5): viewer cannot trigger ranking
    job.assert_not_called()


def test_refresh_ranking_foreign_brand_404(
    app_client: TestClient, t1_token: str, seeded_brands: None
) -> None:
    # t1 requesting b2 (owned by t2): collapses to 404, never confirming b2 exists.
    with patch("gw_geo.api.routers.brands.run_ranking_refresh_job") as job:
        r = app_client.post(
            "/brands/b2/ranking/refresh",
            json={"engines": ["perplexity"]},
            headers={"Authorization": f"Bearer {t1_token}"},
        )
    assert r.status_code == 404
    job.assert_not_called()


# --- POST /brands/{id}/attribution/reconcile (W4 attribution-reconcile trigger) --------------


def test_reconcile_attribution_enqueues_job(
    app_client: TestClient, editor_token: str, seeded_brands: None
) -> None:
    # The TestClient runs the enqueued BackgroundTask before returning, so a patched
    # run_attribution_reconcile_job records that it was scheduled -- with no live DB work.
    with patch("gw_geo.api.routers.brands.run_attribution_reconcile_job") as job:
        r = app_client.post(
            "/brands/b1/attribution/reconcile",
            json={"since": "2026-06-01", "until": "2026-07-02"},
            headers={"Authorization": f"Bearer {editor_token}"},
        )
    assert r.status_code == 202
    assert r.json() == {"status": "accepted", "brand_id": "b1"}
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "t1"  # from the token, never the client
    assert kwargs["brand_id"] == "b1"
    assert kwargs["since"] == "2026-06-01"
    assert kwargs["until"] == "2026-07-02"


def test_reconcile_attribution_defaults_when_body_omitted(
    app_client: TestClient, editor_token: str, seeded_brands: None
) -> None:
    with patch("gw_geo.api.routers.brands.run_attribution_reconcile_job") as job:
        r = app_client.post(
            "/brands/b1/attribution/reconcile",
            headers={"Authorization": f"Bearer {editor_token}"},
        )
    assert r.status_code == 202
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["since"] is None and kwargs["until"] is None  # job resolves its default window


def test_reconcile_attribution_requires_editor(
    app_client: TestClient, viewer_token: str, seeded_brands: None
) -> None:
    with patch("gw_geo.api.routers.brands.run_attribution_reconcile_job") as job:
        r = app_client.post(
            "/brands/b1/attribution/reconcile",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
    assert r.status_code == 403  # RBAC gate (ui-spec §5): viewer cannot trigger reconcile
    job.assert_not_called()


def test_reconcile_attribution_foreign_brand_404(
    app_client: TestClient, t1_token: str, seeded_brands: None
) -> None:
    # t1 requesting b2 (owned by t2): collapses to 404, never confirming b2 exists.
    with patch("gw_geo.api.routers.brands.run_attribution_reconcile_job") as job:
        r = app_client.post(
            "/brands/b2/attribution/reconcile",
            headers={"Authorization": f"Bearer {t1_token}"},
        )
    assert r.status_code == 404
    job.assert_not_called()
