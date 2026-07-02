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
