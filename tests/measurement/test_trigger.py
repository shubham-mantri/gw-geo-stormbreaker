"""Tests for the local measurement trigger job (W2 live wiring).

Hermetic (TRD §12): `build_runtime` + `run_measurement` are patched, so this exercises only how
`run_measurement_job` resolves its defaults and wires a fresh session into `run_measurement` --
no live adapters, S3, or network calls, and no real DB (the sqlite URL is never queried because
`run_measurement` is mocked).
"""

from unittest.mock import AsyncMock, patch

from gw_geo.common.config import Settings
from gw_geo.measurement import trigger


def _runtime() -> dict[str, object]:
    return {"extractor": object(), "archive": object(), "engines": ["perplexity"]}


def test_run_measurement_job_wires_session_and_resolves_defaults() -> None:
    settings = Settings(database_url="sqlite://", default_n_samples=8, default_geos=["us"])
    with (
        patch("gw_geo.measurement.trigger.get_settings", return_value=settings),
        patch("gw_geo.measurement.trigger.build_runtime", return_value=_runtime()) as br,
        patch("gw_geo.measurement.trigger.run_measurement", new=AsyncMock(return_value=[])) as run,
    ):
        result = trigger.run_measurement_job(tenant_id="t1", brand_id="b1", engines=["perplexity"])

    assert result == []
    br.assert_called_once_with(settings)
    kwargs = run.await_args.kwargs
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["brand_id"] == "b1"
    assert kwargs["engines"] == ["perplexity"]
    assert kwargs["geos"] == ["us"]  # from settings.default_geos
    assert kwargs["personas"] == [None]  # default: unpersonalized
    assert kwargs["n_samples"] == 8  # from settings.default_n_samples
    assert kwargs["extractor"] is not None  # threaded from build_runtime
    assert kwargs["archive"] is not None
    # date defaults to today's ISO date (YYYY-MM-DD)
    assert isinstance(kwargs["date"], str)
    assert len(kwargs["date"]) == 10


def test_run_measurement_job_honors_explicit_overrides() -> None:
    settings = Settings(database_url="sqlite://")
    with (
        patch("gw_geo.measurement.trigger.get_settings", return_value=settings),
        patch("gw_geo.measurement.trigger.build_runtime", return_value=_runtime()),
        patch("gw_geo.measurement.trigger.run_measurement", new=AsyncMock(return_value=[])) as run,
    ):
        trigger.run_measurement_job(
            tenant_id="t1",
            brand_id="b1",
            engines=["openai"],
            geos=["gb"],
            n_samples=3,
            date="2026-07-01",
        )

    kwargs = run.await_args.kwargs
    assert kwargs["engines"] == ["openai"]
    assert kwargs["geos"] == ["gb"]
    assert kwargs["n_samples"] == 3
    assert kwargs["date"] == "2026-07-01"
