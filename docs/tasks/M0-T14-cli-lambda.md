# M0-T14 — CLI + Lambda entrypoint

**Depends on:** T13 · **Wave:** 3 · **Suggested agent:** general-purpose

**Goal:** Two ways to run M0: a CLI for local dev and a Lambda handler for deploy. Both wire real
adapters/extractor/archive from settings and call `run_measurement`. This closes M0's
definition of done.

**Files:**
- Create: `src/gw_geo/cli.py`, `src/gw_geo/handlers/run_measurement.py`,
  `src/gw_geo/common/wiring.py`
- Test: `tests/test_cli.py`

## Interfaces

```python
# common/wiring.py — build real dependencies from Settings (registers adapters, S3 archive, Claude extractor)
def build_runtime(settings) -> dict: ...   # {"extractor":..., "archive":..., "engines":[...]}

# cli.py
def main(argv: list[str] | None = None) -> int: ...
# usage: python -m gw_geo.cli measure --brand <id> --engines perplexity,openai --n 8 [--geo us]

# handlers/run_measurement.py
def handler(event: dict, context) -> dict: ...
# event: {"tenant_id","brand_id","engines":[...],"geos":[...],"n_samples":int}
```

## Steps
- [ ] **1. Failing test** `tests/test_cli.py` (patch `run_measurement` + `build_runtime`, assert
  argument parsing + exit code, no real work):

```python
from unittest.mock import patch, AsyncMock
from gw_geo import cli

def test_cli_parses_and_invokes():
    with patch("gw_geo.cli.build_runtime", return_value={"extractor":object(),
               "archive":object(),"engines":["perplexity"]}), \
         patch("gw_geo.cli.run_measurement", new=AsyncMock(return_value=[])) as run:
        rc = cli.main(["measure","--brand","b1","--engines","perplexity","--n","4"])
    assert rc == 0
    kwargs = run.await_args.kwargs
    assert kwargs["brand_id"]=="b1" and kwargs["n_samples"]==4 and kwargs["engines"]==["perplexity"]
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `wiring.py`, `cli.py` (argparse; open a DB session from
  `settings.database_url`; `asyncio.run(run_measurement(...))`; print snapshots as JSON), and the
  Lambda `handler` (same wiring; returns snapshot dicts).
- [ ] **4. Run → pass**; mypy clean on `common/wiring.py`.
- [ ] **5. Add** a `functions:` entry in `serverless.yml` for `run_measurement.handler`.
- [ ] **6. Commit:** `feat(cli): measurement CLI + lambda handler`

## Acceptance
- `python -m gw_geo.cli measure --brand <id> --engines perplexity,openai --n 8` runs the pipeline
  and prints snapshots; Lambda handler invokes the same path; **M0 definition of done met.**
