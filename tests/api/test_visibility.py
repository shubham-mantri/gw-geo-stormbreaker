"""Tests for the visibility + sources read endpoints (M2-T14, ui-spec.md §6/§3.2/§3.3).

Fixtures (``app_client``, ``t1_token``, ``t2_token``, ``seeded_snapshots``, ``seeded_citations``)
live in ``tests/api/conftest.py``. Hermetic: in-memory SQLite, no live calls.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_visibility_exposes_confidence(
    app_client: TestClient, t1_token: str, seeded_snapshots: None
) -> None:
    r = app_client.get(
        "/brands/b1/visibility?range=30d&geo=us",
        headers={"Authorization": f"Bearer {t1_token}"},
    )
    assert r.status_code == 200
    eng = r.json()["engines"][0]
    assert {"engine", "mention_rate", "ci", "cited", "avg_position", "sentiment", "n_samples"} <= (
        set(eng)
    )
    assert len(eng["ci"]) == 2 and eng["n_samples"] >= 1  # CI + sample size present


def test_sources_shape(app_client: TestClient, t1_token: str, seeded_citations: None) -> None:
    r = app_client.get(
        "/brands/b1/sources?range=30d",
        headers={"Authorization": f"Bearer {t1_token}"},
    )
    row = r.json()[0]
    assert {"domain", "source_type", "you_pct", "competitor_pcts"} <= set(row)


def test_visibility_tenant_isolation(app_client: TestClient, t2_token: str) -> None:
    assert (
        app_client.get(
            "/brands/b1/visibility", headers={"Authorization": f"Bearer {t2_token}"}
        ).status_code
        == 404
    )
