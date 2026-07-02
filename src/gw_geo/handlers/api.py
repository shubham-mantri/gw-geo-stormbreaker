"""AWS Lambda entrypoint for the REST API (m2-design.md §3).

Mangum wraps the FastAPI ASGI app onto the same Lambda / API-Gateway target used by the M0 handlers
(one deploy path, no divergence). ``create_app()`` here uses env-driven :func:`get_settings`;
building the app is import-safe -- the DB engine is created lazily and opens no connection at import
time.
"""

from __future__ import annotations

from mangum import Mangum

from gw_geo.api.app import create_app

handler = Mangum(create_app())
