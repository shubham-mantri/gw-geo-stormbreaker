"""Local-dev CLI entrypoint for the M0 measurement pipeline and M3 ranking pipeline (TRD §11).

    python -m gw_geo.cli measure --brand <id> --engines perplexity,openai --n 8 [--geo us]
    python -m gw_geo.cli rank --brand <id> --engines perplexity,openai --input ranking_input.json
    python -m gw_geo.cli schedule [--brand <id> --tenant <id>] --engines perplexity --interval 24h

`measure` wires real dependencies via `gw_geo.common.wiring.build_runtime` and drives the same
`gw_geo.measurement.runner.run_measurement` pipeline that the Lambda handler
(`gw_geo.handlers.run_measurement`) invokes. `rank` wires real dependencies via
`build_ranking_inputs` (this module) and drives `gw_geo.ranking.runner.run_ranking` (M3-T20).
`schedule` is a pure local process (an `asyncio.sleep` loop -- NO Lambda/EventBridge) that
re-runs `gw_geo.measurement.trigger.run_measurement_job` for one brand, one tenant's brands, or
every brand, every `--interval`.

Every pipeline entry point (`build_runtime`, `run_measurement`, `run_measurement_job`,
`build_ranking_inputs`, `run_ranking`) is imported by name into this module (rather than
referenced through its owning module) so tests can patch it as `gw_geo.cli.<name>`.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from gw_geo.common.config import Settings, get_settings
from gw_geo.common.db import Brand
from gw_geo.common.models import FeatureVector, SourceType
from gw_geo.common.wiring import build_runtime, configured_engine_names
from gw_geo.measurement.runner import run_measurement
from gw_geo.measurement.trigger import run_measurement_job
from gw_geo.ranking.model import make_backend
from gw_geo.ranking.runner import run_ranking


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

    rank = subparsers.add_parser(
        "rank", help="Train per-engine ranking models and emit recommendation reports"
    )
    rank.add_argument("--brand", required=True, help="Brand id to rank")
    rank.add_argument(
        "--tenant", default="default", help="Tenant id that owns the brand (default: %(default)s)"
    )
    rank.add_argument(
        "--engines",
        required=True,
        help="Comma-separated engine names to rank, e.g. perplexity,openai (each must have an "
        "entry under --input's candidates/current/source_mix)",
    )
    rank.add_argument(
        "--input",
        required=True,
        help=(
            "Path to a JSON file supplying this run's per-engine ranking inputs: "
            '{"candidates": {engine: [{"url": str, "features": {...FeatureVector fields...}}]}, '
            '"current": {engine: {...FeatureVector fields...}}, '
            '"source_mix": {engine: {source_type_value: fraction}}}. Candidate/current feature '
            "vectors and the source mix are produced upstream (feature extraction, citation "
            "aggregation) -- this command trains and persists the ranking model from them; it "
            "does not itself crawl content or call an embedding model."
        ),
    )

    schedule = subparsers.add_parser(
        "schedule",
        help="Loop measurement runs locally on an interval (no Lambda/EventBridge)",
    )
    schedule.add_argument(
        "--brand",
        default=None,
        help="Brand id to measure each cycle; omit to measure all brands in the DB",
    )
    schedule.add_argument(
        "--tenant",
        default=None,
        help=(
            "With --brand, the tenant that owns it (default: 'default'); without --brand, "
            "narrows the all-brands sweep to one tenant"
        ),
    )
    schedule.add_argument(
        "--engines",
        default=None,
        help="Comma-separated engine names to probe (default: every API-keyed engine configured)",
    )
    schedule.add_argument(
        "--geo",
        default=None,
        help="Comma-separated geos to probe (default: settings.default_geos)",
    )
    schedule.add_argument(
        "--n",
        dest="n_samples",
        type=int,
        default=None,
        help="Samples per prompt per (engine, geo, persona) group (default: settings.default_n_samples)",
    )
    schedule.add_argument(
        "--interval",
        default="24h",
        help="Delay between cycles: e.g. 24h, 30m, 3600s, or a bare number of seconds (default: %(default)s)",
    )
    schedule.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle then exit (rather than looping forever)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse `argv` and dispatch to the requested subcommand's real-dependency-wired runner.

    Returns the process exit code (`0` on success; `1` for an unrecognized command, which
    `argparse`'s `required=True` subparsers already make unreachable in practice).
    """
    args = _build_parser().parse_args(argv)
    if args.command == "measure":
        return _run_measure(args)
    if args.command == "rank":
        return _run_rank(args)
    if args.command == "schedule":
        return _run_schedule(args)
    return 1


def _run_measure(args: argparse.Namespace) -> int:
    """Wire real dependencies and run one M0 measurement pass synchronously.

    Prints the resulting `VisibilitySnapshot`s as JSON to stdout and returns the process exit
    code (`0` on success).
    """
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


def build_ranking_inputs(settings: Settings, input_path: str) -> dict[str, Any]:
    """Load `rank`'s per-engine ranking inputs and build the real, config-selected backend factory.

    Candidate/current `FeatureVector`s and each engine's citation-source mix are not sourced
    live by this CLI: no crawler/embedder/citation-aggregation pipeline is wired in here (out of
    scope for M3-T20 -- `run_ranking`'s contract takes them pre-built, per m3-design §2.6), so
    they are read from the JSON file at `input_path`, shaped as:

        {"candidates": {engine: [{"url": str, "features": {...FeatureVector fields...}}]},
         "current": {engine: {...FeatureVector fields...}},
         "source_mix": {engine: {source_type_value: fraction}}}

    `backend_factory` is real: `ranking.model.make_backend`, keyed off `settings.
    ranking_model_type` (TRD §8 / m3-design §2.3) -- the one client `run_ranking` actually
    injects. Returns a dict of the four `run_ranking` keyword arguments this function is
    responsible for building: `candidates_by_engine`, `current_by_engine`,
    `source_mix_by_engine`, `backend_factory`.
    """
    with open(input_path, encoding="utf-8") as fh:
        payload = json.load(fh)

    candidates_by_engine = {
        engine: [
            {"url": candidate["url"], "features": FeatureVector(**candidate["features"])}
            for candidate in candidates
        ]
        for engine, candidates in payload.get("candidates", {}).items()
    }
    current_by_engine = {
        engine: FeatureVector(**fields) for engine, fields in payload.get("current", {}).items()
    }
    source_mix_by_engine = {
        engine: {SourceType(source): weight for source, weight in mix.items()}
        for engine, mix in payload.get("source_mix", {}).items()
    }
    return {
        "candidates_by_engine": candidates_by_engine,
        "current_by_engine": current_by_engine,
        "source_mix_by_engine": source_mix_by_engine,
        "backend_factory": lambda: make_backend(settings.ranking_model_type),
    }


def _run_rank(args: argparse.Namespace) -> int:
    """Wire real dependencies and run one ranking pass synchronously.

    Prints the resulting `RankingReport`s (one per engine) as JSON to stdout and returns the
    process exit code (`0` on success).
    """
    settings = get_settings()
    engines: list[str] = [e.strip() for e in args.engines.split(",") if e.strip()]
    inputs = build_ranking_inputs(settings, args.input)

    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        reports = run_ranking(
            session=session,
            tenant_id=args.tenant,
            brand_id=args.brand,
            engines=engines,
            candidates_by_engine=inputs["candidates_by_engine"],
            backend_factory=inputs["backend_factory"],
            current_by_engine=inputs["current_by_engine"],
            source_mix_by_engine=inputs["source_mix_by_engine"],
            model_type=settings.ranking_model_type,
        )
    finally:
        session.close()

    print(json.dumps({e: r.model_dump(mode="json") for e, r in reports.items()}, indent=2))
    return 0


def _parse_interval(value: str) -> float:
    """Parse a schedule interval into seconds.

    Accepts an `h`/`m`/`s`-suffixed value (`"24h"`, `"30m"`, `"3600s"`) or a bare number of
    seconds (`"3600"`). Raises `ValueError` on anything else (via `float`).
    """
    text = value.strip().lower()
    units = {"h": 3600.0, "m": 60.0, "s": 1.0}
    if text and text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text)


def _resolve_targets(settings: Settings, args: argparse.Namespace) -> list[tuple[str, str]]:
    """Resolve the `(tenant_id, brand_id)` pairs this schedule run measures.

    An explicit `--brand` measures just that brand (tenant defaults to `"default"`). Otherwise
    every `Brand` in the DB is measured -- optionally narrowed to `--tenant` -- which is the
    "all active brands" sweep (a `Brand` row is the unit of an active brand; there is no separate
    enabled flag in the schema).
    """
    if args.brand:
        return [(args.tenant or "default", args.brand)]

    engine = create_engine(settings.database_url)
    session = Session(engine)
    try:
        query = session.query(Brand)
        if args.tenant:
            query = query.filter(Brand.tenant_id == args.tenant)
        return [(brand.tenant_id, brand.id) for brand in query.order_by(Brand.id).all()]
    finally:
        session.close()


async def _schedule_loop(settings: Settings, args: argparse.Namespace) -> None:
    """The local scheduler loop: measure the resolved targets, then `asyncio.sleep(interval)`.

    `run_measurement_job` owns its own event loop (`asyncio.run`), so it must NOT be awaited
    inline on this already-running loop -- each job is dispatched to a thread executor, which is
    what keeps the job's internal `asyncio.run` from raising "cannot be called from a running
    event loop". `--once` runs exactly one cycle then returns.
    """
    interval = _parse_interval(args.interval)
    engines: list[str] = (
        [e.strip() for e in args.engines.split(",") if e.strip()]
        if args.engines
        else configured_engine_names(settings)
    )
    geos: list[str] | None = (
        [g.strip() for g in args.geo.split(",") if g.strip()] if args.geo else None
    )
    loop = asyncio.get_running_loop()

    while True:
        for tenant_id, brand_id in _resolve_targets(settings, args):
            await loop.run_in_executor(
                None,
                functools.partial(
                    run_measurement_job,
                    tenant_id=tenant_id,
                    brand_id=brand_id,
                    engines=engines,
                    geos=geos,
                    n_samples=args.n_samples,
                ),
            )
        if args.once:
            return
        await asyncio.sleep(interval)


def _run_schedule(args: argparse.Namespace) -> int:
    """Run the local measurement scheduler loop (pure local process; no Lambda/EventBridge)."""
    settings = get_settings()
    asyncio.run(_schedule_loop(settings, args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
