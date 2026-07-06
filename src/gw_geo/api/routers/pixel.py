"""Local pixel-serving router (W4): ``GET /pixel/gwgeo.js`` -- serve the lead-capture pixel bundle
from the LOCAL app, with no CDN.

The pixel SDK source lives in ``web/pixel/gwgeo.ts`` and is bundled+minified to
``web/public/gwgeo.js`` by ``npm --prefix web run build:pixel`` (esbuild). This route reads that
built bundle off local disk and serves it as ``application/javascript`` so the install snippet
(``GET /lead-capture/snippet``) can point a customer's ``<script src>`` at *this* backend instead of
a third-party CDN -- keeping the whole lead-capture path self-hosted and local (m2-design §6; PRD
NG1 white-hat: a first-party analytics beacon served first-party).

Public + unauthenticated, like the sibling ``leadcapture`` beacon: the bundle is ordinary
client-side JS meant to be loaded from arbitrary customer pages, so it carries no tenant data and
needs no JWT. It reads nothing from the DB either -- only the on-disk build artifact.

Where the bundle is read from is ``settings.pixel_js_path`` when set, else the in-repo
``web/public/gwgeo.js`` resolved relative to this package (:data:`_DEFAULT_PIXEL_JS`), so a local
``uvicorn`` run serves the freshly-built pixel with zero extra config. If the bundle has not been
built yet the route returns a **404** whose detail names the exact build command, rather than a bare
500 -- the honest "not built" signal for a fresh checkout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from gw_geo.api.deps import get_settings_dep
from gw_geo.common.config import Settings

router = APIRouter(tags=["pixel"])

# This file is ``<repo>/src/gw_geo/api/routers/pixel.py``; parents[4] is the repo root, so the
# default built bundle is ``<repo>/web/public/gwgeo.js`` -- exactly where ``build:pixel`` writes it.
# Overridable via ``settings.pixel_js_path`` (e.g. a packaged/deployed copy outside the repo tree).
_DEFAULT_PIXEL_JS = Path(__file__).resolve().parents[4] / "web" / "public" / "gwgeo.js"

# One hour: the bundle is a versioned static asset that changes only on a rebuild+redeploy, so a
# short-but-real cache header is safe (and avoids a fetch on every customer pageview) while still
# picking up a new build within the hour. Deliberately not immutable/long-lived -- the served file
# can change in place under a local rebuild.
_CACHE_CONTROL = "public, max-age=3600"


def _resolve_pixel_path(settings: Settings) -> Path:
    """The on-disk path of the built pixel bundle: ``settings.pixel_js_path`` if set, else the
    in-repo ``web/public/gwgeo.js`` (:data:`_DEFAULT_PIXEL_JS`)."""
    return Path(settings.pixel_js_path) if settings.pixel_js_path else _DEFAULT_PIXEL_JS


@router.get("/pixel/gwgeo.js")
def serve_pixel(settings: Annotated[Settings, Depends(get_settings_dep)]) -> Response:
    """``GET /pixel/gwgeo.js`` (W4) -- serve the locally-built lead-capture pixel bundle.

    Public/unauthenticated static JS (no tenant data, no DB read). Reads the built bundle from
    :func:`_resolve_pixel_path` and returns it as ``application/javascript`` with a modest
    cache header. A missing bundle (never built) is a **404** naming the build command, not a 500.
    """
    path = _resolve_pixel_path(settings)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="pixel bundle not built; run `npm --prefix web run build:pixel`",
        ) from exc
    return Response(
        content=content,
        media_type="application/javascript",
        headers={"Cache-Control": _CACHE_CONTROL},
    )
