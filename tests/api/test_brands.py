"""Tests for the brands + overview endpoints (M2-T13, ui-spec.md §6/§3.1) and the
``POST /brands/{id}/measure`` live-measurement trigger (W2 live wiring).

Fixtures (``app_client``, ``t1_token``, ``t2_token``, ``viewer_token``, ``editor_token``,
``seeded_brands``, ``seeded_snapshots``) live in ``tests/api/conftest.py``. Hermetic: in-memory
SQLite, no live calls -- the trigger tests patch ``run_measurement_job`` so the enqueued background
task never builds a real runtime or probes an engine.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from gw_geo.api.routers import brands
from gw_geo.ranking.fetch import FetchedPage


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


# --- POST /brands/suggest (M5 domain-first onboarding auto-fill) ------------------------------


class _FakeFetcher:
    """A `PageFetcher` returning a canned `FetchedPage` -- no live HTTP (the SSRF-guarded real one is
    swapped out via the `get_brand_suggest_deps` override)."""

    def __init__(self, page: FetchedPage | None) -> None:
        self._page = page

    def fetch(self, url: str) -> FetchedPage | None:
        return self._page


class _FakeLLM:
    """An `LLMClient` returning one canned dict for every stage -- no live LLM call.

    The suggest pipeline calls it four times (profile -> draft -> critique -> seed-prompts); the
    shared ``{name, competitors:[{name}], prompts:[...]}`` shape parses cleanly at each stage
    (profile reads ``name``, draft/critique read ``competitors``, seed-prompts reads ``prompts``),
    so a single fake drives the whole flow end-to-end here.
    """

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._result


def _wire_suggest(
    client: TestClient,
    *,
    page: FetchedPage | None,
    competitors: list[dict[str, str]],
    name: str = "Acme",
    seed_prompts: list[str] | None = None,
) -> None:
    """Point `/brands/suggest`'s injected fetcher + research/critic LLMs at hermetic fakes."""
    fake = _FakeLLM({"name": name, "competitors": competitors, "prompts": seed_prompts or []})
    deps = brands.BrandSuggestDeps(fetcher=_FakeFetcher(page), llm=fake, critic=fake)
    client.app.dependency_overrides[brands.get_brand_suggest_deps] = lambda: deps


def test_suggest_starts_a_job_and_transitions_running_to_done(
    app_client: TestClient, editor_token: str
) -> None:
    # POST *starts* the async job (202 + job_id). The TestClient runs the enqueued BackgroundTask
    # before returning, so by the time we poll the (hermetic-fake) pipeline has completed -> done,
    # carrying the refined suggestion. name comes from the profile stage; competitors from critique;
    # seed_prompts from the seed-prompt stage -- all surfaced in the result the client polls.
    _wire_suggest(
        app_client,
        page=FetchedPage(text="<head><title>Acme | The best CRM</title></head>"),
        competitors=[{"name": "Beta"}, {"name": "Gamma"}],
        seed_prompts=["best CRM for startups", "top CRMs for small teams"],
    )
    started = app_client.post(
        "/brands/suggest",
        json={"domain": "acme.com"},
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert started.status_code == 202  # static /brands/suggest path matched, not a {brand_id} route
    job_id = started.json()["job_id"]
    assert job_id  # a non-empty job id to poll

    status = app_client.get(
        f"/brands/suggest/status/{job_id}",
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "done"
    assert body["stage"] == "done"
    assert body["result"] == {
        "name": "Acme",
        "domain": "acme.com",
        "competitors": ["Beta", "Gamma"],
        "seed_prompts": ["best CRM for startups", "top CRMs for small teams"],
    }
    assert body["error"] is None


def test_suggest_no_db_write(app_client: TestClient, editor_token: str) -> None:
    # Pure read/suggest: starting a suggestion for a domain must not create a brand.
    _wire_suggest(app_client, page=None, competitors=[])
    r = app_client.post(
        "/brands/suggest",
        json={"domain": "acme.com"},
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert r.status_code == 202
    listed = app_client.get("/brands", headers={"Authorization": f"Bearer {editor_token}"})
    assert listed.json() == []  # nothing persisted


def test_suggest_requires_auth(app_client: TestClient) -> None:
    # No token -> 401 before the body runs, so no job is started (and no live fetch/LLM even with
    # the real default deps).
    r = app_client.post("/brands/suggest", json={"domain": "acme.com"})
    assert r.status_code == 401


def test_suggest_requires_editor(app_client: TestClient, viewer_token: str) -> None:
    # Same principal requirement as POST /brands: a viewer -> 403 (no job started).
    r = app_client.post(
        "/brands/suggest",
        json={"domain": "acme.com"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403


def test_suggest_status_unknown_job_404(app_client: TestClient, editor_token: str) -> None:
    # An unknown/expired job id collapses to 404 (the client then restarts the lookup).
    r = app_client.get(
        "/brands/suggest/status/does-not-exist",
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert r.status_code == 404


def test_suggest_status_requires_editor(app_client: TestClient, viewer_token: str) -> None:
    # The status endpoint carries the same RBAC gate as the start endpoint (viewer -> 403).
    r = app_client.get(
        "/brands/suggest/status/whatever",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert r.status_code == 403


def test_suggest_error_path_stores_error_and_never_5xxs(
    app_client: TestClient, editor_token: str
) -> None:
    # If the (normally-total) pipeline raised, the runner records status="error"; the endpoints
    # never 5xx -- the client silently falls back to manual entry.
    _wire_suggest(app_client, page=None, competitors=[])
    with patch(
        "gw_geo.onboarding.jobs.suggest_brand_details",
        side_effect=RuntimeError("pipeline boom"),
    ):
        started = app_client.post(
            "/brands/suggest",
            json={"domain": "acme.com"},
            headers={"Authorization": f"Bearer {editor_token}"},
        )
    assert started.status_code == 202
    job_id = started.json()["job_id"]

    status = app_client.get(
        f"/brands/suggest/status/{job_id}",
        headers={"Authorization": f"Bearer {editor_token}"},
    )
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "error"
    assert body["result"] is None
    assert body["error"] and "pipeline boom" in body["error"]


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
