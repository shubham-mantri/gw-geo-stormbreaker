"""M1 Measurement-GA end-to-end runner validation (docs/tasks/M1-T19-contract-completeness-ga.md).

Hermetic proof that `run_measurement` drives a **mixed** engine fleet -- one API adapter
(`GeminiAdapter`, its HTTP call served from a recorded fixture via `respx`) and one Playwright
consumer-surface adapter (`AIOverviewsAdapter`, its page served by a `FakeCaptureClient` over a
recorded HTML fixture) -- through the full probe -> parse -> aggregate -> persist pipeline in a
single pass, producing and persisting one `VisibilitySnapshot` per engine. No live API, browser,
or AWS call is made (TRD §12): the SQLite session, the in-memory archive, and both fixture-backed
adapters keep the whole run in-process. This closes M1's definition of done (m1-design.md §1, §8).
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator

import httpx
import pytest
import respx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.capture.base import CapturePage
from gw_geo.common.db import Base, Brand, Prompt, Tenant, VisibilitySnapshot
from gw_geo.measurement.probe import base
from gw_geo.measurement.probe.ai_overviews import AIOverviewsAdapter
from gw_geo.measurement.probe.gemini import GeminiAdapter
from gw_geo.measurement.runner import run_measurement
from tests.capture.fakes import FakeCaptureClient

_FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "answers"
_GEMINI_FIXTURE = json.loads((_FIXTURES_DIR / "gemini_api.json").read_text())
_AI_OVERVIEWS_HTML = (_FIXTURES_DIR / "google_ai_overviews.html").read_text()


class StubExtractor:
    """Deterministic extractor: every answer mentions the brand at position 1 (no live LLM)."""

    def extract(self, text: str, brand: object) -> dict[str, object]:
        return {
            "brand_mentioned": True,
            "position": 1,
            "sentiment": "positive",
            "competitors_present": [],
        }


class MemArchive:
    """In-memory raw-payload sink standing in for S3 (TRD §12: hermetic default suite)."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    def put(self, key: str, payload: object) -> str:
        self.store[key] = payload
        return key


@pytest.fixture
def seeded_session() -> Iterator[Session]:
    """In-memory SQLite session: Tenant t1 (ample daily budget), Brand b1, 2 prompts."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)

    session.add(Tenant(id="t1", name="Acme", sampling_budget_daily=10.0))
    session.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com"))
    session.add_all(
        [
            Prompt(id="p1", tenant_id="t1", brand_id="b1", text="best crm for startups?"),
            Prompt(id="p2", tenant_id="t1", brand_id="b1", text="top crm tools?"),
        ]
    )
    session.commit()

    yield session

    session.close()


@respx.mock
async def test_ga_multi_engine_snapshot(seeded_session: Session) -> None:
    """A mixed API+Playwright fleet yields one persisted snapshot per engine, fully hermetic."""
    respx.route(
        method="POST",
        url__regex=r"https://generativelanguage\.googleapis\.com/.*:generateContent",
    ).mock(return_value=httpx.Response(200, json=_GEMINI_FIXTURE))

    base.clear_registry()
    base.register(GeminiAdapter(api_key="k", client=httpx.AsyncClient()))
    base.register(
        AIOverviewsAdapter(
            capture=FakeCaptureClient(
                {
                    "google_ai_overviews": CapturePage(
                        html=_AI_OVERVIEWS_HTML,
                        final_url="https://www.google.com/search?q=best+crm",
                    )
                }
            )
        )
    )

    snaps = await run_measurement(
        session=seeded_session,
        tenant_id="t1",
        brand_id="b1",
        engines=["gemini", "google_ai_overviews"],
        geos=["us"],
        personas=[None],
        n_samples=2,
        extractor=StubExtractor(),
        archive=MemArchive(),
        date="2026-07-02",
    )

    # One snapshot per engine, across the mixed API + Playwright fleet.
    assert {s.engine for s in snaps} == {"gemini", "google_ai_overviews"}
    assert all(s.n_samples > 0 and s.ci_low <= s.ci_high for s in snaps)

    # The snapshots were actually persisted (probe -> parse -> aggregate -> persist ran end to end).
    persisted = {row.engine for row in seeded_session.query(VisibilitySnapshot).all()}
    assert persisted == {"gemini", "google_ai_overviews"}
