"""CaptureClient seam (m1-design.md S3.1) -- the DI boundary that keeps Playwright hermetic.

`CapturePage` is the result of a single capture (a recorded fixture or a live fetch);
`CaptureClient` is the Protocol every capture backend implements: `FakeCaptureClient`
(tests, recorded HTML fixtures) and `LiveCaptureClient` (M1-T16, the real fleet). Playwright
adapters (M1-T11..T13) depend only on this Protocol -- never on Playwright directly -- so the
default test suite never needs a browser.
"""

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class CapturePage(BaseModel):
    html: str
    final_url: str
    screenshots: list[str] = Field(default_factory=list)  # optional S3 refs
    meta: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class CaptureClient(Protocol):
    async def fetch(
        self, query: str, *, surface: str, geo: str, persona: str | None
    ) -> CapturePage: ...
