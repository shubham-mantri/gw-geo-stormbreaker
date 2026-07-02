"""Tests for the brands + overview endpoints (M2-T13, ui-spec.md §6/§3.1).

Fixtures (``app_client``, ``t1_token``, ``t2_token``, ``viewer_token``, ``seeded_brands``,
``seeded_snapshots``) live in ``tests/api/conftest.py``. Hermetic: in-memory SQLite, no live calls.
"""

from __future__ import annotations

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
