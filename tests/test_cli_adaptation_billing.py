"""CLI tests for the M5 adaptation + billing subcommands (`adapt`, `reward-reconcile`,
`close-billing`).

Hermetic (TRD §12): each job is patched, so these exercise only argument parsing + delegation --
no DB, LLM, or network. The jobs are imported by name into `gw_geo.cli`, so they are patched as
`gw_geo.cli.<name>` (mirrors the `reconcile`/`seed-discover` CLI tests).
"""

from unittest.mock import patch

from gw_geo import cli
from gw_geo.orchestration.scheduler import CycleResult


def test_adapt_delegates_with_brand_tenant_window_budget() -> None:
    with patch(
        "gw_geo.cli.run_adaptation_job", return_value=CycleResult(tasks_spawned=2)
    ) as job:
        rc = cli.main(
            ["adapt", "--brand", "b1", "--tenant", "t1",
             "--since", "2026-06-01", "--until", "2026-06-30", "--budget", "3"]
        )
    assert rc == 0
    kwargs = job.call_args.kwargs
    assert kwargs["brand_id"] == "b1"
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["since"] == "2026-06-01"
    assert kwargs["until"] == "2026-06-30"
    assert kwargs["budget"] == 3


def test_adapt_defaults() -> None:
    with patch("gw_geo.cli.run_adaptation_job", return_value=CycleResult()) as job:
        rc = cli.main(["adapt", "--brand", "b1"])
    assert rc == 0
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "default"
    assert kwargs["since"] is None and kwargs["until"] is None
    assert kwargs["budget"] is None


def test_reward_reconcile_delegates_with_aging_days() -> None:
    with patch("gw_geo.cli.run_reward_reconcile_job", return_value=4) as job:
        rc = cli.main(
            ["reward-reconcile", "--brand", "b1", "--tenant", "t1", "--aging-days", "21"]
        )
    assert rc == 0
    kwargs = job.call_args.kwargs
    assert kwargs["brand_id"] == "b1"
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["aging_days"] == 21


def test_reward_reconcile_defaults() -> None:
    with patch("gw_geo.cli.run_reward_reconcile_job", return_value=0) as job:
        rc = cli.main(["reward-reconcile", "--brand", "b1"])
    assert rc == 0
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "default"
    assert kwargs["aging_days"] == 14  # default aging window


def test_close_billing_delegates_with_period() -> None:
    with patch(
        "gw_geo.cli.run_billing_close_job",
        return_value={"invoice_id": "inv1", "total": 510.0, "status": "draft"},
    ) as job:
        rc = cli.main(
            ["close-billing", "--tenant", "t1",
             "--period-start", "2026-06-01", "--period-end", "2026-07-01"]
        )
    assert rc == 0
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["period_start"] == "2026-06-01"
    assert kwargs["period_end"] == "2026-07-01"


def test_close_billing_tenant_defaults() -> None:
    with patch(
        "gw_geo.cli.run_billing_close_job",
        return_value={"invoice_id": "inv1", "total": 0.0, "status": "draft"},
    ) as job:
        rc = cli.main(
            ["close-billing", "--period-start", "2026-06-01", "--period-end", "2026-07-01"]
        )
    assert rc == 0
    assert job.call_args.kwargs["tenant_id"] == "default"
