"""Hermetic `CaptureClient` test double (docs/tasks/M1-T07-capture-seam.md).

Serves a recorded `CapturePage` fixture keyed by `surface`; never touches a real browser,
proxy, or network, so Playwright-adapter tests (M1-T11..T13) stay hermetic.
"""

from gw_geo.capture.base import CapturePage


class FakeCaptureClient:
    """Serves a recorded HTML fixture keyed by (surface, geo, persona)."""

    def __init__(self, pages: dict[str, CapturePage]) -> None:
        self._pages = pages

    async def fetch(
        self, query: str, *, surface: str, geo: str, persona: str | None
    ) -> CapturePage:
        return self._pages[surface]
