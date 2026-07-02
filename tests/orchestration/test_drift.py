"""Drift canary tests (TRD §5.6, m1-design §4, docs/tasks/M1-T14-drift-canary.md).

Hermetic (TRD §12): a fake in-memory engine adapter, a stub extractor, an in-memory raw
archive, and a SQLite session -- no live API/AWS calls. Covers the spec's breach scenario (a
drop beyond `threshold` writes a `drift_event` row with `retrain_flag=True` and reports
`breached=True`) plus the required non-breach counterpart (observed >= baseline writes no
`drift_event` row and reports `breached=False`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from gw_geo.common.db import Base, DriftEvent
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe import base
from gw_geo.orchestration import drift


class DriftyAdapter:
    """Never mentions the canary brand -- simulates an engine that has drifted."""

    name = "gemini"
    supports_citations = True

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        return ProbeResult(engine="gemini", answer_text="no brands here", cited_urls=[])


class SteadyAdapter:
    """Always mentions the canary brand -- simulates an engine that has not drifted."""

    name = "gemini"
    supports_citations = True

    async def probe(
        self, prompt: str, *, geo: str = "us", persona: str | None = None
    ) -> ProbeResult:
        return ProbeResult(engine="gemini", answer_text="Foo is great", cited_urls=[])


class NoMentionExtractor:
    def extract(self, answer_text: str, brand: Any) -> dict[str, Any]:
        return {
            "brand_mentioned": False,
            "position": None,
            "sentiment": "neutral",
            "competitors_present": [],
        }


class MentionExtractor:
    def extract(self, answer_text: str, brand: Any) -> dict[str, Any]:
        return {
            "brand_mentioned": True,
            "position": 1,
            "sentiment": "positive",
            "competitors_present": [],
        }


class MemArchive:
    def put(self, key: str, payload: dict[str, Any]) -> str:
        return key


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def _one_canary() -> list[drift.Canary]:
    return [
        drift.Canary(
            canary_id="c1", engine="gemini", prompt="best crm?", brand="Foo", baseline_rate=0.9
        )
    ]


async def test_breach_writes_event_and_flags_retrain(monkeypatch) -> None:
    base.clear_registry()
    base.register(DriftyAdapter())
    monkeypatch.setattr(drift, "load_canaries", lambda session=None: _one_canary())

    s = _session()
    results = await drift.run_drift_canary(
        s,
        engines=["gemini"],
        threshold=0.2,
        extractor=NoMentionExtractor(),
        archive=MemArchive(),
        date="2026-07-02",
    )

    assert results[0].breached is True and results[0].drop > 0.2
    rows = s.execute(select(DriftEvent)).scalars().all()
    assert len(rows) == 1 and rows[0].retrain_flag is True and rows[0].engine == "gemini"


async def test_non_breach_writes_no_event(monkeypatch) -> None:
    """Required non-breach counterpart: observed >= baseline -> no DriftEvent, breached=False."""
    base.clear_registry()
    base.register(SteadyAdapter())
    monkeypatch.setattr(drift, "load_canaries", lambda session=None: _one_canary())

    s = _session()
    results = await drift.run_drift_canary(
        s,
        engines=["gemini"],
        threshold=0.2,
        extractor=MentionExtractor(),
        archive=MemArchive(),
        date="2026-07-02",
    )

    assert results[0].breached is False
    assert results[0].observed_rate >= results[0].baseline_rate
    rows = s.execute(select(DriftEvent)).scalars().all()
    assert rows == []
