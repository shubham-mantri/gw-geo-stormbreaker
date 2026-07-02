"""CLI `rank` subcommand tests (docs/tasks/M3-T20-ranking-runner-cli.md).

Hermetic (TRD §12): `build_ranking_inputs` and `run_ranking` are patched, so this exercises only
`cli.main`'s `rank` argument parsing and how it invokes the ranking pipeline -- no real DB, model
training, or file I/O. Mirrors `tests/test_cli.py`'s patch-based style for `measure`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from gw_geo import cli

_FAKE_INPUTS: dict[str, Any] = {
    "candidates_by_engine": {},
    "current_by_engine": {},
    "source_mix_by_engine": {},
    "backend_factory": lambda: None,
}


def test_cli_rank_parses_and_invokes():
    with (
        patch("gw_geo.cli.build_ranking_inputs", return_value=_FAKE_INPUTS),
        patch("gw_geo.cli.run_ranking", return_value={}) as run,
    ):
        rc = cli.main(
            ["rank", "--brand", "b1", "--engines", "perplexity", "--input", "unused.json"]
        )
    assert rc == 0
    kwargs = run.call_args.kwargs
    assert kwargs["brand_id"] == "b1" and kwargs["tenant_id"] == "default"
    assert kwargs["engines"] == ["perplexity"]
    assert kwargs["backend_factory"] is _FAKE_INPUTS["backend_factory"]


def test_cli_rank_accepts_multiple_engines_and_tenant():
    with (
        patch("gw_geo.cli.build_ranking_inputs", return_value=_FAKE_INPUTS) as build_inputs,
        patch("gw_geo.cli.run_ranking", return_value={}) as run,
    ):
        rc = cli.main(
            [
                "rank",
                "--brand",
                "b1",
                "--tenant",
                "t9",
                "--engines",
                "perplexity,openai",
                "--input",
                "unused.json",
            ]
        )
    assert rc == 0
    kwargs = run.call_args.kwargs
    assert kwargs["engines"] == ["perplexity", "openai"]
    assert kwargs["tenant_id"] == "t9"
    build_inputs.assert_called_once()
    assert build_inputs.call_args.args[1] == "unused.json"
