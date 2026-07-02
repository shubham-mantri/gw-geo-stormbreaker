"""Local-dev CLI entrypoint for the M0 measurement pipeline (TRD §11).

    python -m gw_geo.cli measure --brand <id> --engines perplexity,openai --n 8 [--geo us]

Wires real dependencies via `gw_geo.common.wiring.build_runtime` and drives the same
`gw_geo.measurement.runner.run_measurement` pipeline that the Lambda handler
(`gw_geo.handlers.run_measurement`) invokes. `build_runtime` and `run_measurement` are imported
by name into this module (rather than referenced through their owning module) so tests can patch
them as `gw_geo.cli.build_runtime` / `gw_geo.cli.run_measurement`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import get_settings
from gw_geo.common.wiring import build_runtime
from gw_geo.measurement.runner import run_measurement


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gw_geo", description="GEO measurement pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    measure = subparsers.add_parser("measure", help="Run a visibility measurement pass")
    measure.add_argument("--brand", required=True, help="Brand id to measure")
    measure.add_argument(
        "--tenant", default="default", help="Tenant id that owns the brand (default: %(default)s)"
    )
    measure.add_argument(
        "--engines",
        required=True,
        help=(
            "Comma-separated engine names to probe, e.g. perplexity,openai,gemini,claude,copilot,"
            "deepseek,google_ai_overviews,chatgpt,grok (an engine with no registered adapter is "
            "skipped; see build_runtime for which are registered under the current config)"
        ),
    )
    measure.add_argument(
        "--geo",
        default=None,
        help="Comma-separated geos to probe (default: settings.default_geos)",
    )
    measure.add_argument(
        "--persona",
        default=None,
        help="Comma-separated personas to probe (default: unpersonalized, i.e. [None])",
    )
    measure.add_argument(
        "--n",
        dest="n_samples",
        type=int,
        default=None,
        help="Samples per prompt per (engine, geo, persona) group (default: settings.default_n_samples)",
    )
    measure.add_argument(
        "--date",
        default=None,
        help="Measurement date as YYYY-MM-DD (default: today, UTC)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse `argv`, wire real dependencies, and run one measurement pass synchronously.

    Prints the resulting `VisibilitySnapshot`s as JSON to stdout and returns the process exit
    code (`0` on success).
    """
    args = _build_parser().parse_args(argv)
    if args.command != "measure":
        return 1

    settings = get_settings()
    runtime = build_runtime(settings)

    engines: list[str] = [e.strip() for e in args.engines.split(",") if e.strip()]
    geos: list[str] = (
        [g.strip() for g in args.geo.split(",") if g.strip()]
        if args.geo
        else list(settings.default_geos)
    )
    personas: list[str | None] = (
        [p.strip() for p in args.persona.split(",") if p.strip()] if args.persona else [None]
    )
    n_samples: int = args.n_samples if args.n_samples is not None else settings.default_n_samples
    date: str = args.date or datetime.now(timezone.utc).date().isoformat()

    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        snapshots = asyncio.run(
            run_measurement(
                session=session,
                tenant_id=args.tenant,
                brand_id=args.brand,
                engines=engines,
                geos=geos,
                personas=personas,
                n_samples=n_samples,
                extractor=runtime["extractor"],
                archive=runtime["archive"],
                date=date,
            )
        )
    finally:
        session.close()

    print(json.dumps([snapshot.model_dump(mode="json") for snapshot in snapshots], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
