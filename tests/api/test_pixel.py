"""Tests for the local pixel-serving route (W4): ``GET /pixel/gwgeo.js``.

Hermetic: a bare FastAPI app mounting only the pixel router, with ``get_settings_dep`` overridden to
point ``pixel_js_path`` at a temp file (no real esbuild build, no CDN, no network). The real build
(``npm --prefix web run build:pixel``) is exercised by the runnable scratch script instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw_geo.api.deps import get_settings_dep
from gw_geo.api.routers import pixel
from gw_geo.common.config import Settings


def _client(settings: Settings) -> TestClient:
    app = FastAPI()
    app.include_router(pixel.router)
    app.dependency_overrides[get_settings_dep] = lambda: settings
    return TestClient(app)


def test_serves_built_bundle_as_javascript(tmp_path: Path) -> None:
    bundle = tmp_path / "gwgeo.js"
    bundle.write_text('"use strict";(()=>{window.gwgeo=()=>{};})();', encoding="utf-8")
    client = _client(Settings(pixel_js_path=str(bundle)))

    r = client.get("/pixel/gwgeo.js")
    assert r.status_code == 200
    assert r.text == '"use strict";(()=>{window.gwgeo=()=>{};})();'
    assert r.headers["content-type"].startswith("application/javascript")
    assert "max-age" in r.headers["cache-control"]


def test_404_when_bundle_not_built(tmp_path: Path) -> None:
    client = _client(Settings(pixel_js_path=str(tmp_path / "does-not-exist.js")))
    r = client.get("/pixel/gwgeo.js")
    assert r.status_code == 404
    assert "build:pixel" in r.json()["detail"]  # names the build command, not a bare 500


def test_default_path_points_at_local_web_public() -> None:
    # With no override, the route reads the in-repo built bundle -- never a CDN.
    resolved = pixel._resolve_pixel_path(Settings(pixel_js_path=""))
    assert resolved == pixel._DEFAULT_PIXEL_JS
    assert resolved.parts[-3:] == ("web", "public", "gwgeo.js")


@pytest.mark.parametrize("configured", ["/abs/path/to/gwgeo.js", "relative/gwgeo.js"])
def test_configured_path_overrides_default(configured: str) -> None:
    resolved = pixel._resolve_pixel_path(Settings(pixel_js_path=configured))
    assert resolved == Path(configured)
