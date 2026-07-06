"""Tests for the ``schedule`` CLI subcommand (W2 local scheduler).

Hermetic (TRD §12): ``run_measurement_job`` is patched, so no live measurement / DB / network
happens. ``--once`` runs exactly one cycle then exits, so the loop is testable without a timer.
"""

import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo import cli
from gw_geo.common.config import Settings
from gw_geo.common.db import Base, Brand, Tenant


@pytest.mark.parametrize(
    "value,expected",
    [("24h", 86400.0), ("3600s", 3600.0), ("3600", 3600.0), ("30m", 1800.0), ("90", 90.0)],
)
def test_parse_interval(value: str, expected: float) -> None:
    assert cli._parse_interval(value) == expected


def test_parse_interval_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        cli._parse_interval("later")


def test_schedule_once_runs_job_for_explicit_brand() -> None:
    settings = Settings(database_url="sqlite://")
    with (
        patch("gw_geo.cli.get_settings", return_value=settings),
        patch("gw_geo.cli.run_measurement_job") as job,
    ):
        rc = cli.main(
            [
                "schedule",
                "--brand",
                "b1",
                "--tenant",
                "t1",
                "--engines",
                "perplexity",
                "--n",
                "4",
                "--interval",
                "24h",
                "--once",
            ]
        )
    assert rc == 0
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["brand_id"] == "b1"
    assert kwargs["engines"] == ["perplexity"]
    assert kwargs["n_samples"] == 4


def test_schedule_runs_sync_job_via_executor_without_nested_loop_error() -> None:
    """Each sync ``run_measurement_job`` (which itself calls ``asyncio.run``) must run in an
    executor, so the job's own ``asyncio.run`` never collides with the scheduler's event loop.

    The fake job below calls ``asyncio.run(...)`` exactly like the real one -- if the scheduler
    awaited it inline on its running loop, this would raise "asyncio.run() cannot be called from
    a running event loop".
    """
    calls: list[dict[str, object]] = []

    def fake_job(**kwargs: object) -> None:
        asyncio.run(asyncio.sleep(0))
        calls.append(kwargs)

    settings = Settings(database_url="sqlite://")
    with (
        patch("gw_geo.cli.get_settings", return_value=settings),
        patch("gw_geo.cli.run_measurement_job", new=fake_job),
    ):
        rc = cli.main(
            ["schedule", "--brand", "b1", "--tenant", "t1", "--engines", "perplexity", "--once"]
        )
    assert rc == 0
    assert len(calls) == 1


def test_schedule_all_brands_queries_db(tmp_path) -> None:
    """With no ``--brand``, the scheduler measures every brand it finds in the DB (all tenants)."""
    db_path = tmp_path / "sched.db"
    url = f"sqlite:///{db_path}"
    eng = create_engine(url)
    Base.metadata.create_all(eng)
    with Session(eng) as session:
        session.add(Tenant(id="t1", name="A", sampling_budget_daily=1.0))
        session.add(Brand(id="b1", tenant_id="t1", name="A", domain="a.com"))
        session.add(Tenant(id="t2", name="B", sampling_budget_daily=1.0))
        session.add(Brand(id="b2", tenant_id="t2", name="B", domain="b.com"))
        session.commit()

    settings = Settings(database_url=url)
    with (
        patch("gw_geo.cli.get_settings", return_value=settings),
        patch("gw_geo.cli.run_measurement_job") as job,
    ):
        rc = cli.main(["schedule", "--engines", "perplexity", "--once"])
    assert rc == 0
    assert job.call_count == 2
    measured = {(c.kwargs["tenant_id"], c.kwargs["brand_id"]) for c in job.call_args_list}
    assert measured == {("t1", "b1"), ("t2", "b2")}
