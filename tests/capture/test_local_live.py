"""Real-browser integration test for `LocalCaptureClient` (M5 local persistent-profile capture).

Marked `@pytest.mark.live` -- deselected by the default gate (`pytest -m "not live"`). This drives
a REAL local browser against a REAL consumer surface using the user's OWN persistent profile, so
it needs manual setup that the default suite never does:

1. Browser binaries: `playwright install chromium` (or the branded `channel`, e.g. Chrome).
2. A persistent profile the user has already logged in to via
   `python -m gw_geo.cli login --surface <chatgpt|grok|google>`.
3. Environment:
     - `GEO_LOCAL_BROWSER_PROFILE_DIR`  the persistent user-data-dir            (REQUIRED)
     - `GEO_LOCAL_SURFACE`              chatgpt|grok|google_ai_overviews         (default "chatgpt")
     - `GEO_LOCAL_BROWSER_CHANNEL`      "chrome"|"msedge"|"" (bundled Chromium)  (default "chrome")
     - `GEO_LOCAL_HEADLESS`             "1" to run headless                      (default headed)

Without `GEO_LOCAL_BROWSER_PROFILE_DIR`, this SKIPS (even under `-m live`) rather than failing, so
running `-m live` in an unconfigured environment reports a clear skip instead of a browser error.
"""

import os

import pytest

from gw_geo.capture.local import LocalCaptureClient

pytestmark = pytest.mark.live


async def test_local_capture_client_fetches_a_real_surface() -> None:
    profile = os.environ.get("GEO_LOCAL_BROWSER_PROFILE_DIR", "")
    if not profile:
        pytest.skip(
            "local capture live test requires GEO_LOCAL_BROWSER_PROFILE_DIR (and a profile "
            "logged in via `python -m gw_geo.cli login`) -- see module docstring"
        )
    surface = os.environ.get("GEO_LOCAL_SURFACE", "chatgpt")
    channel = os.environ.get("GEO_LOCAL_BROWSER_CHANNEL", "chrome") or None
    headless = os.environ.get("GEO_LOCAL_HEADLESS", "0") == "1"

    client = LocalCaptureClient(user_data_dir=profile, channel=channel, headless=headless)
    try:
        page = await client.fetch(
            "best crm for a 10-person startup", surface=surface, geo="us", persona=None
        )
        assert page.html
        assert page.final_url.startswith("http")
    finally:
        await client.aclose()
