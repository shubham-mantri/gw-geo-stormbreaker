"""CLI argument-parsing + wiring tests (docs/tasks/M0-T14-cli-lambda.md).

Hermetic (TRD §12): `build_runtime` and `run_measurement` are patched, so this exercises only
`cli.main`'s argument parsing and how it invokes the pipeline -- no real adapters, DB, or network
calls. Reformatted from the task spec's compact/semicolon-joined body into ruff-clean,
one-statement-per-line form; every assertion below is identical to the spec.
"""

from unittest.mock import AsyncMock, patch

from gw_geo import cli


def test_cli_parses_and_invokes():
    with (
        patch(
            "gw_geo.cli.build_runtime",
            return_value={"extractor": object(), "archive": object(), "engines": ["perplexity"]},
        ),
        patch("gw_geo.cli.run_measurement", new=AsyncMock(return_value=[])) as run,
    ):
        rc = cli.main(["measure", "--brand", "b1", "--engines", "perplexity", "--n", "4"])
    assert rc == 0
    kwargs = run.await_args.kwargs
    assert kwargs["brand_id"] == "b1" and kwargs["n_samples"] == 4
    assert kwargs["engines"] == ["perplexity"]


def test_cli_accepts_new_m1_engines():
    """`--engines` accepts the M1 engine names (e.g. gemini) and threads them to run_measurement."""
    with (
        patch(
            "gw_geo.cli.build_runtime",
            return_value={"extractor": object(), "archive": object(), "engines": ["gemini"]},
        ),
        patch("gw_geo.cli.run_measurement", new=AsyncMock(return_value=[])) as run,
    ):
        rc = cli.main(["measure", "--brand", "b1", "--engines", "gemini,chatgpt", "--n", "2"])
    assert rc == 0
    kwargs = run.await_args.kwargs
    assert kwargs["engines"] == ["gemini", "chatgpt"]


def test_cli_opportunities_invokes_refresh_job():
    """`opportunities` delegates to run_opportunity_refresh_job with the brand + tenant, rc 0."""
    with patch("gw_geo.cli.run_opportunity_refresh_job", return_value=3) as job:
        rc = cli.main(
            ["opportunities", "--brand", "demo-brand", "--tenant", "demo-tenant"]
        )
    assert rc == 0
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["brand_id"] == "demo-brand"
    assert kwargs["tenant_id"] == "demo-tenant"


def test_cli_opportunities_tenant_defaults():
    """`--tenant` defaults to 'default' (matching measure/rank) when omitted."""
    with patch("gw_geo.cli.run_opportunity_refresh_job", return_value=0) as job:
        rc = cli.main(["opportunities", "--brand", "b1"])
    assert rc == 0
    assert job.call_args.kwargs["tenant_id"] == "default"


def test_cli_reconcile_invokes_reconcile_job():
    """`reconcile` delegates to run_attribution_reconcile_job with brand/tenant/window, rc 0."""
    with patch(
        "gw_geo.cli.run_attribution_reconcile_job",
        return_value={"direct": 1, "citation_linked": 0, "assisted": 0},
    ) as job:
        rc = cli.main(
            [
                "reconcile",
                "--brand",
                "b1",
                "--tenant",
                "t1",
                "--since",
                "2026-06-01",
                "--until",
                "2026-07-02",
            ]
        )
    assert rc == 0
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["brand_id"] == "b1"
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["since"] == "2026-06-01"
    assert kwargs["until"] == "2026-07-02"


def test_cli_reconcile_window_defaults_to_none():
    """`--since`/`--until` default to None (the job resolves its own trailing window)."""
    with patch(
        "gw_geo.cli.run_attribution_reconcile_job",
        return_value={"direct": 0, "citation_linked": 0, "assisted": 0},
    ) as job:
        rc = cli.main(["reconcile", "--brand", "b1"])
    assert rc == 0
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "default"
    assert kwargs["since"] is None and kwargs["until"] is None
