"""CLI `rank-live` subcommand tests (M5): source candidates from the citation pool + rank.

Hermetic (TRD §12): `run_ranking_refresh_job` is patched, so this exercises only `cli.main`'s
`rank-live` argument parsing + how it invokes/prints the ranking pipeline -- no real DB, crawl,
embedding, or model training. Mirrors `tests/test_cli_rank.py`'s patch-based style, and asserts the
existing `rank` command is untouched.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from gw_geo import cli
from gw_geo.common.models import RankingReport


def test_cli_rank_live_parses_and_prints_reports(capsys: pytest.CaptureFixture[str]) -> None:
    reports = {"perplexity": RankingReport(engine="perplexity")}
    with patch("gw_geo.cli.run_ranking_refresh_job", return_value=reports) as job:
        rc = cli.main(["rank-live", "--brand", "b1", "--engines", "perplexity,openai"])
    assert rc == 0
    job.assert_called_once()
    kwargs = job.call_args.kwargs
    assert kwargs["brand_id"] == "b1" and kwargs["tenant_id"] == "default"
    assert kwargs["engines"] == ["perplexity", "openai"]
    printed = json.loads(capsys.readouterr().out)
    assert printed["perplexity"]["engine"] == "perplexity"


def test_cli_rank_live_accepts_tenant() -> None:
    with patch("gw_geo.cli.run_ranking_refresh_job", return_value={}) as job:
        rc = cli.main(["rank-live", "--brand", "b1", "--tenant", "t9", "--engines", "perplexity"])
    assert rc == 0
    kwargs = job.call_args.kwargs
    assert kwargs["tenant_id"] == "t9" and kwargs["engines"] == ["perplexity"]


def test_cli_rank_still_uses_input_file_not_the_crawler() -> None:
    # rank-live must not disturb the operator-JSON `rank` command (it never touches the refresh job).
    with (
        patch("gw_geo.cli.build_ranking_inputs", return_value={
            "candidates_by_engine": {}, "current_by_engine": {},
            "source_mix_by_engine": {}, "backend_factory": lambda: None,
        }),
        patch("gw_geo.cli.run_ranking", return_value={}) as run,
        patch("gw_geo.cli.run_ranking_refresh_job") as refresh,
    ):
        rc = cli.main(["rank", "--brand", "b1", "--engines", "perplexity", "--input", "x.json"])
    assert rc == 0
    run.assert_called_once()
    refresh.assert_not_called()
