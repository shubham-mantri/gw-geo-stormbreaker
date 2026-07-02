# gw-geo-stormbreaker

GEO / AI-search **visibility · attribution · execution** service for the Stormbreaker platform.

Gets Gushwork clients **found and recommended by AI answer engines** (ChatGPT, Gemini,
Perplexity, Google AI Overviews/AI Mode, Copilot, Claude, Grok, DeepSeek), **executes** the
content that earns those citations, and **attributes** the resulting leads and pipeline —
closing the loop that measurement-only incumbents (Profound, Athena, Scrunch) leave open.

> **Full product spec:** [`docs/prd.md`](docs/prd.md)

---

## Why this exists

Incumbents tell a brand *"you're mentioned 14% of the time"* and stop. This service adds the
two things they don't: it **does the work** (grounded content generation + on-site publishing +
off-site seeding) and **proves the money** (citation → visitor → captured lead → pipeline).
North-star metric: **attributed pipeline influenced by AI search**.

## Place in the Stormbreaker platform

Follows the `gw-*-stormbreaker` conventions (see `gw-stormbreaker-platform/CLAUDE.md`).
This is the **Python backend service** for the GEO system. API, UI, and DB-migration
concerns will be separate repos per platform convention when we reach those milestones.

- **Stack (target):** Python 3.13 · AWS Lambda + Step Functions · Serverless Framework ·
  PostgreSQL · Pinecone · S3 — matching `gw-backend-stormbreaker`.
- **Reuses:** Gushwork's existing content-generation, publishing, and lead-capture stack.

## Architecture (7 subsystems)

| Package | Subsystem | PRD § |
|---|---|---|
| `src/gw_geo/measurement/` | Measurement harness — Discover → Probe → Parse → Sample/Aggregate (system of record) | 6.1 |
| `src/gw_geo/attribution/` | Attribution engine — citation → visitor → lead → pipeline (**the wedge**) | 6.2 |
| `src/gw_geo/ranking/` | Feature/Rank ML — what content earns citations, per engine | 6.3 |
| `src/gw_geo/content/` | Content engine (on-site) — grounded generation + guardrails + publish | 6.4 |
| `src/gw_geo/seeding/` | Off-site seeding — place content on cited sources (white-hat only) | 6.5 |
| `src/gw_geo/orchestration/` | Orchestration + self-adaptation — run the loop, detect drift, retrain | 6.6 |
| `src/gw_geo/common/` | Shared — data models, DB, config, engine registry | 7 |

See [`docs/architecture.md`](docs/architecture.md) for the data-flow diagram.

## Roadmap (v1 arc)

- **M0** — Foundations: data platform, 1–2 engine adapters, capture + storage proven.
- **M1** — Measurement GA: ≥8 engines, sampling/aggregation with CIs, drift canary. *(Independently shippable.)*
- **M2** — Attribution: referral capture + citation-to-page + CRM/GA4 + holdout framework.
- **M3** — Execution (on-site): knowledge base, grounded generation, guardrails, publish.
- **M4** — Execution (off-site) + self-adaptation + RaaS pricing pilot.

## Status

🚧 **Scaffold only.** Package structure, PRD, and config are in place. No feature code yet —
implementation begins after the M0 plan is written (see the implementation plan, next step).

## Development

```bash
# from repo root (once dependencies are pinned in M0)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Layout

```
gw-geo-stormbreaker/
├── docs/            # prd.md, architecture.md
├── src/gw_geo/      # the 7 subsystem packages (see table above)
├── tests/
├── pyproject.toml
└── serverless.yml   # skeleton; fleshed out in M0
```
