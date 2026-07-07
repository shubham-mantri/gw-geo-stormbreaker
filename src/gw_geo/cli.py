"""Local-dev CLI entrypoint for the M0 measurement pipeline and M3 ranking pipeline (TRD §11).

    python -m gw_geo.cli measure --brand <id> --engines perplexity,openai --n 8 [--geo us]
    python -m gw_geo.cli rank --brand <id> --engines perplexity,openai --input ranking_input.json
    python -m gw_geo.cli rank-live --brand <id> --engines perplexity,openai
    python -m gw_geo.cli schedule [--brand <id> --tenant <id>] --engines perplexity --interval 24h
    python -m gw_geo.cli opportunities --brand <id> [--tenant <id>]
    python -m gw_geo.cli reconcile --brand <id> [--tenant <id>] [--since YYYY-MM-DD --until ...]
    python -m gw_geo.cli seed-discover --brand <id> [--tenant <id>] [--since ... --until ... --budget N]

`measure` wires real dependencies via `gw_geo.common.wiring.build_runtime` and drives the same
`gw_geo.measurement.runner.run_measurement` pipeline that the Lambda handler
(`gw_geo.handlers.run_measurement`) invokes. `rank` wires real dependencies via
`build_ranking_inputs` (this module) and drives `gw_geo.ranking.runner.run_ranking` (M3-T20).
`rank-live` (M5) sources candidates from the citation pool instead of an operator JSON file --
crawling the cited URLs for content + features -- via
`gw_geo.orchestration.ranking_gen.run_ranking_refresh_job`, the same local job the
`POST /brands/{id}/ranking/refresh` endpoint schedules. `schedule` is a pure local process (an `asyncio.sleep` loop -- NO Lambda/EventBridge) that
re-runs `gw_geo.measurement.trigger.run_measurement_job` for one brand, one tenant's brands, or
every brand, every `--interval`. `opportunities` (re)generates a brand's ranked opportunity queue
from its live visibility data via `gw_geo.orchestration.opportunity_gen.run_opportunity_refresh_job`
-- the same local job the `POST /brands/{id}/opportunities/refresh` endpoint schedules. `reconcile`
runs the fuzzy attribution writers (direct/citation/assisted) over a brand's captured sessions+leads
and persists the `attribution_link` rows the pipeline reads, via
`gw_geo.attribution.trigger.run_attribution_reconcile_job` -- the same local job the
`POST /brands/{id}/attribution/reconcile` endpoint schedules. `seed-discover` discovers off-site
seeding targets from a brand's citation-source mix and opens human-in-the-loop `seeding_task`s
(white-hat: it drafts briefs only and NEVER posts or places -- tasks stop at `todo`/`briefed`), via
`gw_geo.seeding.trigger.run_seeding_discovery_job`.

Every pipeline entry point (`build_runtime`, `run_measurement`, `run_measurement_job`,
`build_ranking_inputs`, `run_ranking`, `run_ranking_refresh_job`, `run_opportunity_refresh_job`,
`run_attribution_reconcile_job`, `run_seeding_discovery_job`) is imported by name into this module
(rather than referenced through its owning module) so tests can patch it as `gw_geo.cli.<name>`.
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

from gw_geo.attribution.trigger import run_attribution_reconcile_job
from gw_geo.billing.trigger import run_billing_close_job
from gw_geo.capture.live import _SURFACE_START_URLS
from gw_geo.capture.local import run_login_session
from gw_geo.common.config import Settings, get_settings
from gw_geo.common.db import Brand
from gw_geo.common.models import FeatureVector, SourceType
from gw_geo.common.wiring import build_runtime, configured_engine_names
from gw_geo.measurement.runner import run_measurement
from gw_geo.measurement.trigger import run_measurement_job
from gw_geo.orchestration.adaptation_trigger import run_adaptation_job
from gw_geo.orchestration.opportunity_gen import run_opportunity_refresh_job
from gw_geo.orchestration.ranking_gen import run_ranking_refresh_job
from gw_geo.orchestration.reward import run_reward_reconcile_job
from gw_geo.ranking.model import make_backend
from gw_geo.ranking.runner import run_ranking
from gw_geo.seeding.trigger import run_seeding_discovery_job


# `login` accepts short, friendly surface names; each maps to the canonical capture start URL
# (reusing `capture.live._SURFACE_START_URLS`, so login and capture can never point at different
# URLs for the same surface).
_LOGIN_START_URLS: dict[str, str] = {
    "chatgpt": _SURFACE_START_URLS["chatgpt"],
    "grok": _SURFACE_START_URLS["grok"],
    "google": _SURFACE_START_URLS["google_ai_overviews"],
}


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

    rank_live = subparsers.add_parser(
        "rank-live",
        help="Source ranking candidates from the citation pool (crawl cited URLs), train "
        "per-engine models, and emit recommendation reports -- no operator JSON needed",
    )
    rank_live.add_argument("--brand", required=True, help="Brand id to rank")
    rank_live.add_argument(
        "--tenant", default="default", help="Tenant id that owns the brand (default: %(default)s)"
    )
    rank_live.add_argument(
        "--engines",
        required=True,
        help="Comma-separated engine names to rank, e.g. perplexity,openai. NOTE: negatives are "
        "sourced cross-engine (a URL another engine cited but this one didn't), so measure >=2 "
        "engines -- a single engine yields all-positive, untrainable labels",
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

    opportunities = subparsers.add_parser(
        "opportunities",
        help="Generate + persist a brand's ranked opportunity queue from live visibility data",
    )
    opportunities.add_argument(
        "--brand", required=True, help="Brand id to (re)generate opportunities for"
    )
    opportunities.add_argument(
        "--tenant", default="default", help="Tenant id that owns the brand (default: %(default)s)"
    )

    reconcile = subparsers.add_parser(
        "reconcile",
        help="Run the fuzzy attribution writers (direct/citation/assisted) over captured "
        "sessions+leads and persist the attribution_link rows the pipeline reads",
    )
    reconcile.add_argument(
        "--brand", required=True, help="Brand id to reconcile attribution for"
    )
    reconcile.add_argument(
        "--tenant", default="default", help="Tenant id that owns the brand (default: %(default)s)"
    )
    reconcile.add_argument(
        "--since",
        default=None,
        help="Inclusive window start YYYY-MM-DD (default: the job's trailing look-back window)",
    )
    reconcile.add_argument(
        "--until",
        default=None,
        help="Inclusive window end YYYY-MM-DD (default: today, UTC)",
    )

    seed_discover = subparsers.add_parser(
        "seed-discover",
        help="Discover off-site seeding targets from the citation-source mix and open "
        "human-in-the-loop tasks (white-hat; drafts briefs only -- NEVER posts or places)",
    )
    seed_discover.add_argument(
        "--brand", required=True, help="Brand id to discover seeding targets for"
    )
    seed_discover.add_argument(
        "--tenant", default="default", help="Tenant id that owns the brand (default: %(default)s)"
    )
    seed_discover.add_argument(
        "--since",
        default=None,
        help="Inclusive window start YYYY-MM-DD (default: the job's trailing look-back window)",
    )
    seed_discover.add_argument(
        "--until",
        default=None,
        help="Inclusive window end YYYY-MM-DD (default: today, UTC)",
    )
    seed_discover.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Max number of targets/tasks to create (highest-priority first; default: unbounded "
        "up to the discovery limit)",
    )

    adapt = subparsers.add_parser(
        "adapt",
        help="Run one measure->sense->adapt cycle (drift canary + retrain-on-breach + off-site "
        "target discovery) locally; opens todo tasks only (white-hat, NEVER posts/places)",
    )
    adapt.add_argument("--brand", required=True, help="Brand id to run the adaptation cycle for")
    adapt.add_argument(
        "--tenant", default="default", help="Tenant id that owns the brand (default: %(default)s)"
    )
    adapt.add_argument(
        "--since", default=None,
        help="Inclusive discovery window start YYYY-MM-DD (default: trailing 90-day window)",
    )
    adapt.add_argument(
        "--until", default=None,
        help="Inclusive discovery window end YYYY-MM-DD (default: today, UTC)",
    )
    adapt.add_argument(
        "--budget", type=int, default=None,
        help="Max new seeding tasks to spawn this cycle (highest-priority first; default: 5)",
    )

    reward_reconcile = subparsers.add_parser(
        "reward-reconcile",
        help="Feed aged (>= --aging-days) off-site placements' corroboration lift back onto the "
        "seeding-effort bandit arms so the bandit accumulates (no posting)",
    )
    reward_reconcile.add_argument(
        "--brand", required=True, help="Brand id to reconcile seeding-effort rewards for"
    )
    reward_reconcile.add_argument(
        "--tenant", default="default", help="Tenant id that owns the brand (default: %(default)s)"
    )
    reward_reconcile.add_argument(
        "--aging-days", type=int, default=14,
        help="Min age (days) of a placed task before its corroboration counts as a reward "
        "(default: %(default)s)",
    )

    close_billing = subparsers.add_parser(
        "close-billing",
        help="Meter + price one billing period and persist a DRAFT invoice (idempotent per "
        "period); NEVER finalizes/sends -- a human does that",
    )
    close_billing.add_argument(
        "--tenant", default="default", help="Tenant id to close the period for (default: %(default)s)"
    )
    close_billing.add_argument(
        "--period-start", required=True, help="Period start YYYY-MM-DD (inclusive)"
    )
    close_billing.add_argument(
        "--period-end", required=True, help="Period end YYYY-MM-DD (exclusive, half-open)"
    )

    login = subparsers.add_parser(
        "login",
        help="One-time: open a persistent local browser profile HEADED at a surface so you can "
        "sign in with your OWN account; cookies persist for later `capture_backend=local` runs",
    )
    login.add_argument(
        "--surface",
        required=True,
        choices=sorted(_LOGIN_START_URLS),
        help="Which surface to sign in to (chatgpt|grok|google)",
    )
    login.add_argument(
        "--profile",
        default=None,
        help="Persistent browser profile dir (default: settings.local_browser_profile_dir)",
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
    if args.command == "rank-live":
        return _run_rank_live(args)
    if args.command == "schedule":
        return _run_schedule(args)
    if args.command == "opportunities":
        return _run_opportunities(args)
    if args.command == "reconcile":
        return _run_reconcile(args)
    if args.command == "seed-discover":
        return _run_seed_discover(args)
    if args.command == "adapt":
        return _run_adapt(args)
    if args.command == "reward-reconcile":
        return _run_reward_reconcile(args)
    if args.command == "close-billing":
        return _run_close_billing(args)
    if args.command == "login":
        return _run_login(args)
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


def _run_rank_live(args: argparse.Namespace) -> int:
    """Source candidates from the citation pool, train per-engine models, and print the reports.

    Delegates to the same `run_ranking_refresh_job` unit the `POST /brands/{id}/ranking/refresh`
    endpoint schedules (which owns its own DB session and wires the live crawler + config-selected
    embedder + model backend), so the CLI and endpoint never diverge. Prints the per-engine
    `RankingReport`s as JSON to stdout and returns the process exit code (`0` on success).
    """
    engines: list[str] = [e.strip() for e in args.engines.split(",") if e.strip()]
    reports = run_ranking_refresh_job(
        tenant_id=args.tenant, brand_id=args.brand, engines=engines
    )
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


def _run_opportunities(args: argparse.Namespace) -> int:
    """(Re)generate a brand's ranked opportunity queue from its live visibility data, locally.

    Delegates to the same `run_opportunity_refresh_job` unit the API endpoint schedules (which owns
    its own DB session), so the CLI and endpoint never diverge. Prints the generated count as JSON
    and returns the process exit code (`0` on success).
    """
    count = run_opportunity_refresh_job(tenant_id=args.tenant, brand_id=args.brand)
    print(json.dumps({"brand_id": args.brand, "opportunities": count}, indent=2))
    return 0


def _run_reconcile(args: argparse.Namespace) -> int:
    """Run the fuzzy attribution writers over a brand's captured sessions+leads, locally.

    Delegates to the same `run_attribution_reconcile_job` unit the API endpoint schedules (which
    owns its own DB session), so the CLI and endpoint never diverge. Prints the per-method link
    counts as JSON and returns the process exit code (`0` on success).
    """
    counts = run_attribution_reconcile_job(
        tenant_id=args.tenant, brand_id=args.brand, since=args.since, until=args.until
    )
    print(json.dumps({"brand_id": args.brand, "attribution_links": counts}, indent=2))
    return 0


def _run_seed_discover(args: argparse.Namespace) -> int:
    """Discover off-site seeding targets and open human-in-the-loop tasks, locally.

    Delegates to the same `run_seeding_discovery_job` unit (which owns its own DB session and wires
    the real citation-source map + optional brief drafting). White-hat: it drafts briefs only and
    NEVER posts or places -- tasks stop at `todo`/`briefed`. Prints the created-task count as JSON
    and returns the process exit code (`0` on success).
    """
    count = run_seeding_discovery_job(
        tenant_id=args.tenant,
        brand_id=args.brand,
        since=args.since,
        until=args.until,
        budget=args.budget,
    )
    print(json.dumps({"brand_id": args.brand, "seeding_tasks": count}, indent=2))
    return 0


def _run_adapt(args: argparse.Namespace) -> int:
    """Run one local measure->sense->adapt cycle for a brand.

    Delegates to the same `run_adaptation_job` unit any future request path would call (which owns
    its own DB session and wires the real drift canary + retrain-on-breach + discovery + workflow).
    White-hat: it opens `todo` seeding tasks only -- it NEVER runs compliance, posts, or places.
    Prints the `CycleResult` as JSON and returns the process exit code (`0` on success).
    """
    result = run_adaptation_job(
        tenant_id=args.tenant,
        brand_id=args.brand,
        since=args.since,
        until=args.until,
        budget=args.budget,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2))
    return 0


def _run_reward_reconcile(args: argparse.Namespace) -> int:
    """Feed aged off-site placements' corroboration lift back onto the seeding-effort bandit, locally.

    Delegates to the same `run_reward_reconcile_job` unit (which owns its own DB session and wires
    the real citation-source map). Prints the number of reward observations recorded as JSON and
    returns the process exit code (`0` on success).
    """
    count = run_reward_reconcile_job(
        tenant_id=args.tenant, brand_id=args.brand, aging_days=args.aging_days
    )
    print(json.dumps({"brand_id": args.brand, "rewards_recorded": count}, indent=2))
    return 0


def _run_close_billing(args: argparse.Namespace) -> int:
    """Meter + price one billing period and persist a DRAFT invoice, locally.

    Delegates to the same `run_billing_close_job` unit (which owns its own DB session, resolves the
    tenant's plan, and wires the real `PipelineAttributionSource`). Idempotent per period; NEVER
    finalizes or sends. Prints the invoice id/total/status as JSON and returns the process exit
    code (`0` on success).
    """
    result = run_billing_close_job(
        tenant_id=args.tenant,
        period_start=args.period_start,
        period_end=args.period_end,
    )
    print(json.dumps(result, indent=2))
    return 0


def _run_login(args: argparse.Namespace) -> int:
    """Open the local persistent browser profile HEADED so the user signs in once (M5).

    Resolves the profile dir (`--profile`, else `settings.local_browser_profile_dir`) and channel
    (`settings.local_browser_channel`, empty -> bundled Chromium) and drives the real browser via
    `run_login_session`, which blocks until the user closes the window. This is the only CLI path
    that launches a real browser; `run_login_session` is patched out in the tests. Returns `0`.
    """
    settings = get_settings()
    profile = args.profile or settings.local_browser_profile_dir
    channel = settings.local_browser_channel or None
    asyncio.run(
        run_login_session(
            user_data_dir=profile,
            channel=channel,
            start_url=_LOGIN_START_URLS[args.surface],
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
