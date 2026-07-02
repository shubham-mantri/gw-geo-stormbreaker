# gw-geo-stormbreaker — Orchestrator Guide

GEO / AI-search visibility · attribution · execution service for the Stormbreaker platform.
This file is the entry point for **subagent-driven implementation**. A fresh session that is told
to "start implementation" should follow the process below.

## The documents (read in this order)
1. [`docs/prd.md`](docs/prd.md) — product requirements (what & why).
2. [`docs/trd.md`](docs/trd.md) — technical design (how). **Interface contracts here are binding.**
3. [`docs/architecture.md`](docs/architecture.md) — data-flow overview.
4. [`docs/tasks/README.md`](docs/tasks/README.md) — the task index, dependency DAG, and wave plan.
5. [`docs/tasks/M0-T*.md`](docs/tasks/) — one self-contained, TDD-first task per file.

## Role: orchestrator (you)
You do **not** write feature code yourself. You dispatch one subagent per task, review its output,
and coordinate waves. Keep everything **local** (local git branches + merges; no cloud, no PRs
unless asked).

### On "start implementation"
1. **Load context:** read `docs/trd.md` and `docs/tasks/README.md` (the DAG + wave plan).
2. **Invoke the sub-skill:** use `superpowers:subagent-driven-development` to run each task with a
   fresh subagent + two-stage review.
3. **Dispatch by wave, parallel within a wave.** Send all tasks in the current wave as
   subagents in a single batch:
   - Wave 0: T01, T02, T03
   - Wave 1: T04, T05, T06, T07
   - Wave 2: T08, T09, T10, T11, T12
   - Wave 3: T13, then T14
   Each subagent gets: "Implement `docs/tasks/M0-T<NN>-*.md` exactly. TDD. Honor the TRD interface
   contracts verbatim. Commit on completion. Return a summary + the commit hash."
4. **Review gate between waves:** before starting the next wave, verify for the finished wave:
   `pytest -q` green, `ruff check` + `mypy src/gw_geo/common` clean, and interfaces match the TRD
   (esp. the `EngineAdapter` contract from T06). Only then dispatch the next wave.
5. **Report** after each wave: which tasks landed, test status, any interface deviations to fix.

### Guardrails
- Do not let a subagent change an interface defined in the TRD without surfacing it to the user.
- Do not start a task whose dependencies (see DAG) haven't merged.
- White-hat only — no grey-hat GEO tactics ever enter this codebase (PRD NG1).
- Conventions (branch `m0/T<NN>-<slug>`, commit format, `Co-Authored-By` trailer, TDD, hermetic
  tests) are in `docs/tasks/README.md` → "Conventions".

## Platform context
Part of the 25+ repo Stormbreaker platform (`~/stormbreaker`, conventions in
`gw-stormbreaker-platform/CLAUDE.md`). This repo is the Python backend service; API/UI/migrations
become sibling `gw-*` repos at M2. Stack: Python 3.13 · Lambda + Step Functions · Postgres ·
Pinecone · S3.

## Quick commands
- `make install` — install deps · `make check` — ruff + mypy + pytest · `make test` — tests.
- Run M0 pipeline (after T14): `python -m gw_geo.cli measure --brand <id> --engines perplexity,openai --n 8`

## Current status
Scaffold + PRD + TRD + M0 task breakdown complete. **No feature code yet.** M0 is ready to
implement — start with Wave 0.
