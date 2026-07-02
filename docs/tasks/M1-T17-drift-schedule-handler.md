# M1-T17 — Drift schedule / handler (EventBridge cron → Lambda)

**Depends on:** T14 (drift canary) · **Wave:** 3 · **Suggested agent:** general-purpose

**Goal:** Run the drift canary daily: a Lambda handler that wires real dependencies and calls
`run_drift_canary`, plus an EventBridge cron trigger in `serverless.yml`. Alert on breach goes to the
SNS topic in deploy / stdout locally (m1-design §4). AWS mocked with `moto` in tests.

**Files:**
- Create: `src/gw_geo/handlers/run_drift.py`
- Modify: `serverless.yml` (add the scheduled function)
- Test: `tests/handlers/__init__.py`, `tests/handlers/test_run_drift.py`

## Interface

```python
# handlers/run_drift.py
def handler(event: dict, context) -> dict: ...
# event (optional overrides): {"engines":[...], "date":"YYYY-MM-DD"}
# builds runtime (DB session, extractor, archive, SNS alert hook) from Settings, calls
# run_drift_canary, returns {"results":[DriftResult...], "breaches": <int>}.

def make_sns_alert_hook(topic_arn: str, *, sns_client=None): ...
# returns a callable alert(drift_result) -> None; publishes to SNS in deploy; injected client in tests.
```

## Steps
- [ ] **1. Failing test** `tests/handlers/test_run_drift.py` (patch `run_drift_canary`; `moto` for SNS):

```python
from unittest.mock import patch, AsyncMock
from gw_geo.orchestration.drift import DriftResult
from gw_geo.handlers import run_drift

def test_handler_invokes_drift_and_counts_breaches():
    fake = [DriftResult(engine="gemini", canary_id="c1", baseline_rate=0.9,
                        observed_rate=0.5, drop=0.4, breached=True)]
    with patch("gw_geo.handlers.run_drift.build_runtime",
               return_value={"extractor": object(), "archive": object(),
                             "engines": ["gemini"], "alert": lambda r: None}), \
         patch("gw_geo.handlers.run_drift.run_drift_canary",
               new=AsyncMock(return_value=fake)):
        out = run_drift.handler({"engines": ["gemini"], "date": "2026-07-02"}, None)
    assert out["breaches"] == 1

def test_sns_alert_hook_publishes(monkeypatch):
    import boto3, moto
    with moto.mock_aws():
        sns = boto3.client("sns", region_name="us-east-1")
        arn = sns.create_topic(Name="drift")["TopicArn"]
        hook = run_drift.make_sns_alert_hook(arn, sns_client=sns)
        hook(DriftResult(engine="gemini", canary_id="c1", baseline_rate=0.9,
                         observed_rate=0.5, drop=0.4, breached=True))   # no raise
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `handler` (open DB session from `settings.database_url`;
  `asyncio.run(run_drift_canary(...))`; count breaches; return dicts) and `make_sns_alert_hook`
  (publish a structured message; injected `sns_client` in tests). Reuse `build_runtime` (T18) or a
  local wiring shim until T18 lands.
- [ ] **4. Run → pass**; mypy clean on touched `common`.
- [ ] **5. Add** to `serverless.yml` a `run_drift` function with an EventBridge daily `schedule:`
  (e.g. `cron(0 6 * * ? *)`) and the SNS topic ARN in the environment.
- [ ] **6. Commit:** `feat(handlers): daily drift canary lambda + eventbridge schedule`

## Acceptance
- `handler` builds runtime, runs the canary, returns breach count; SNS alert hook publishes under
  `moto` (no live AWS); `serverless.yml` has a daily scheduled `run_drift` function; hermetic tests.
