"""End-to-end runner tests (TRD §5.5, docs/tasks/M0-T13-runner.md).

Hermetic (TRD §12): a fake in-memory engine adapter, a stub extractor and an in-memory raw
archive -- no live API/AWS calls. Covers the happy path (one snapshot per engine/geo/persona)
and graceful degradation when the tenant's daily sampling budget is exhausted (TRD §7).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.db import (
    AnswerExtraction,
    Base,
    Brand,
    Citation,
    Prompt,
    ProbeRun,
    Tenant,
    VisibilitySnapshot,
)
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe import base
from gw_geo.measurement.runner import run_measurement


class FakeAdapter:
    name = "fake"
    supports_citations = True

    async def probe(self, prompt, *, geo="us", persona=None):
        return ProbeResult(
            engine="fake",
            answer_text="Foo is best",
            cited_urls=["https://foo.com"],
            cost_usd=0.001,
        )


class StubExtractor:
    def extract(self, text, brand):
        return {
            "brand_mentioned": True,
            "position": 1,
            "sentiment": "positive",
            "competitors_present": [],
        }


class MemArchive:
    def __init__(self):
        self.store = {}

    def put(self, key, payload):
        self.store[key] = payload
        return key


@pytest.fixture
def seeded_session() -> Iterator[Session]:
    """In-memory SQLite session: Tenant t1 (`sampling_budget_daily=1.0`), Brand b1, 3 prompts."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)

    session.add(Tenant(id="t1", name="Acme", sampling_budget_daily=1.0))
    session.add(Brand(id="b1", tenant_id="t1", name="Acme", domain="acme.com"))
    session.add_all(
        [
            Prompt(id="p1", tenant_id="t1", brand_id="b1", text="best crm for startups?"),
            Prompt(id="p2", tenant_id="t1", brand_id="b1", text="top crm tools?"),
            Prompt(id="p3", tenant_id="t1", brand_id="b1", text="crm recommendations?"),
        ]
    )
    session.commit()

    yield session

    session.close()


async def test_runner_produces_snapshot(seeded_session):
    base.clear_registry()
    base.register(FakeAdapter())
    snaps = await run_measurement(
        session=seeded_session,
        tenant_id="t1",
        brand_id="b1",
        engines=["fake"],
        geos=["us"],
        personas=[None],
        n_samples=4,
        extractor=StubExtractor(),
        archive=MemArchive(),
        date="2026-07-02",
    )
    assert len(snaps) == 1
    s = snaps[0]
    assert s.engine=="fake" and s.n_samples == 3*4 and s.mention_rate == 1.0
    assert s.ci_low <= 1.0 <= s.ci_high


async def test_runner_persists_rows_and_upserts_citations(seeded_session):
    """Acceptance: the run persists ProbeRun/Extraction/Snapshot rows and upserts citations."""
    archive = MemArchive()
    base.clear_registry()
    base.register(FakeAdapter())
    await run_measurement(
        session=seeded_session,
        tenant_id="t1",
        brand_id="b1",
        engines=["fake"],
        geos=["us"],
        personas=[None],
        n_samples=4,
        extractor=StubExtractor(),
        archive=archive,
        date="2026-07-02",
    )

    assert seeded_session.query(ProbeRun).count() == 3 * 4
    assert seeded_session.query(AnswerExtraction).count() == 3 * 4
    assert seeded_session.query(VisibilitySnapshot).count() == 1
    assert len(archive.store) == 3 * 4

    # The 12 probes all cite the same URL -> a single upserted row with seen_count bumped to 12.
    citations = seeded_session.query(Citation).all()
    assert len(citations) == 1
    assert citations[0].url == "https://foo.com"
    assert citations[0].domain == "foo.com"
    assert citations[0].seen_count == 3 * 4

    probe = seeded_session.query(ProbeRun).first()
    assert probe.status == "ok"
    assert probe.cost_usd == 0.001
    assert probe.raw_answer_s3_key is not None


async def test_runner_degrades_when_budget_exhausted(seeded_session):
    """A near-zero daily budget yields a partial/empty result, never a crash (TRD §7)."""
    tenant = seeded_session.get(Tenant, "t1")
    tenant.sampling_budget_daily = 0.0001
    seeded_session.commit()

    base.clear_registry()
    base.register(FakeAdapter())
    snaps = await run_measurement(
        session=seeded_session,
        tenant_id="t1",
        brand_id="b1",
        engines=["fake"],
        geos=["us"],
        personas=[None],
        n_samples=4,
        extractor=StubExtractor(),
        archive=MemArchive(),
        date="2026-07-02",
    )
    assert snaps == []
