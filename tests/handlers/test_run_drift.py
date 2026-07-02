"""Drift schedule Lambda handler tests (m1-design §4, docs/tasks/M1-T17-drift-schedule-handler.md).

Hermetic (TRD §12): `build_runtime` and `run_drift_canary` are patched by name -- see
`gw_geo.handlers.run_drift`'s module docstring for why they're imported directly into that
module -- and the SNS alert hook is exercised against `moto`'s `mock_aws`. No live AWS/API calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import boto3
from moto import mock_aws

from gw_geo.handlers import run_drift
from gw_geo.orchestration.drift import DriftResult


def _breach() -> DriftResult:
    return DriftResult(
        engine="gemini",
        canary_id="c1",
        baseline_rate=0.9,
        observed_rate=0.5,
        drop=0.4,
        breached=True,
    )


def test_handler_invokes_drift_and_counts_breaches() -> None:
    fake = [_breach()]
    with (
        patch(
            "gw_geo.handlers.run_drift.build_runtime",
            return_value={
                "extractor": object(),
                "archive": object(),
                "engines": ["gemini"],
                "alert": lambda r: None,
            },
        ),
        patch(
            "gw_geo.handlers.run_drift.run_drift_canary",
            new=AsyncMock(return_value=fake),
        ),
    ):
        out = run_drift.handler({"engines": ["gemini"], "date": "2026-07-02"}, None)

    assert out["breaches"] == 1
    assert out["results"] == [fake[0].model_dump()]


@mock_aws
def test_sns_alert_hook_publishes_under_moto() -> None:
    sns = boto3.client("sns", region_name="us-east-1")
    arn = sns.create_topic(Name="drift")["TopicArn"]
    hook = run_drift.make_sns_alert_hook(arn, sns_client=sns)

    hook(_breach())  # no raise


def test_sns_alert_hook_is_noop_with_no_topic_arn() -> None:
    hook = run_drift.make_sns_alert_hook("")

    hook(_breach())  # no raise, no SNS client ever constructed
