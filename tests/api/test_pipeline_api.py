"""Tests for the pipeline + alerts read endpoints (M2-T15, ui-spec.md §6/§3.6/§3.7).

Fixtures (``app_client``, ``t1_token``, ``t2_token``, ``seeded_full_attribution``,
``seeded_drift``) live in ``tests/api/conftest.py``. Hermetic: in-memory SQLite, no live calls.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_pipeline_has_method_breakdown_and_note(
    app_client: TestClient, t1_token: str, seeded_full_attribution: None
) -> None:
    r = app_client.get(
        "/brands/b1/pipeline?range=90d", headers={"Authorization": f"Bearer {t1_token}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body["method_breakdown"]) == {
        "direct",
        "citation_linked",
        "assisted",
        "holdout_incremental",
    }
    assert body["confidence_note"]  # honesty rule: never empty
    assert body["attributed"] <= body["influenced"]


def test_alerts_shape(app_client: TestClient, t1_token: str, seeded_drift: None) -> None:
    r = app_client.get("/brands/b1/alerts", headers={"Authorization": f"Bearer {t1_token}"})
    a = r.json()[0]
    assert a["severity"] in ("red", "green", "yellow") and a["message"]


def test_pipeline_tenant_isolation(app_client: TestClient, t2_token: str) -> None:
    assert (
        app_client.get(
            "/brands/b1/pipeline", headers={"Authorization": f"Bearer {t2_token}"}
        ).status_code
        == 404
    )
