"""CLI `seed-discover` subcommand tests (m4 seeding live-wiring).

Hermetic (TRD S12): `run_seeding_discovery_job` is patched, so this exercises only argument parsing
and delegation -- no DB, LLM, or network. Mirrors the `reconcile`/`opportunities` CLI tests: the
job is imported by name into `gw_geo.cli`, so it is patched as `gw_geo.cli.run_seeding_discovery_job`.
"""

from unittest.mock import patch

from gw_geo import cli


def test_seed_discover_delegates_with_brand_tenant_window_budget() -> None:
    with patch("gw_geo.cli.run_seeding_discovery_job", return_value=3) as job:
        rc = cli.main(
            [
                "seed-discover",
                "--brand", "b1",
                "--tenant", "t1",
                "--since", "2026-06-01",
                "--until", "2026-06-30",
                "--budget", "5",
            ]
        )
    assert rc == 0
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["brand_id"] == "b1"
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["since"] == "2026-06-01"
    assert kwargs["until"] == "2026-06-30"
    assert kwargs["budget"] == 5


def test_seed_discover_defaults() -> None:
    # --tenant defaults to "default"; window + budget default to None (job resolves its own window).
    with patch("gw_geo.cli.run_seeding_discovery_job", return_value=0) as job:
        rc = cli.main(["seed-discover", "--brand", "b1"])
    assert rc == 0
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "default"
    assert kwargs["since"] is None and kwargs["until"] is None
    assert kwargs["budget"] is None
