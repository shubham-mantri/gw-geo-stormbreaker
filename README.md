# gw-geo-stormbreaker

A standalone product for **AI-search visibility · attribution · execution**.

Gets a brand **found and recommended by AI answer engines** (ChatGPT, Gemini, Perplexity,
Google AI Overviews/AI Mode, Copilot, Claude, Grok, DeepSeek), **executes** the content that
earns those citations, and **attributes** the resulting leads and pipeline — closing the loop
that measurement-only incumbents (Profound, Athena, Scrunch) leave open.

> This is an independent, self-contained project. It does not depend on or integrate with any
> other codebase — its own stack, auth, storage, and deploy.

> **Full product spec:** [`docs/prd.md`](docs/prd.md) · **Technical design:** [`docs/trd.md`](docs/trd.md)
> · **UI spec:** [`docs/ui-spec.md`](docs/ui-spec.md)

---

## Why this exists

Incumbents tell a brand *"you're mentioned 14% of the time"* and stop. This product adds the
two things they don't: it **does the work** (grounded content generation + on-site publishing +
off-site seeding) and **proves the money** (citation → visitor → captured lead → pipeline).
North-star metric: **attributed pipeline influenced by AI search**.

## Shape of the project

- **`backend`** (this repo root, Python) — the measurement/attribution/execution engine + API.
- **`web`** (planned) — a standalone Next.js dashboard, the end-user product. See
  [`docs/ui-spec.md`](docs/ui-spec.md).
- **Stack:** Python 3.13 · async workers (Lambda or containers) · PostgreSQL · a vector store
  (pgvector or Pinecone) · S3-compatible object storage · Next.js/React frontend.
- Self-contained auth (JWT + lightweight RBAC, or a hosted provider like Clerk/Auth0) — no
  external/shared auth service.

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
- **M2** — Attribution + **the dashboard UI goes live**: referral capture + citation-to-page + CRM/GA4 + holdout framework.
- **M3** — Execution (on-site): knowledge base, grounded generation, guardrails, publish.
- **M4** — Execution (off-site) + self-adaptation + RaaS pricing pilot.

## Status

✅ **M0 implemented** — the measurement pipeline (models, DB + tenant scoping, config, cost
governor, engine-adapter registry, Perplexity + OpenAI adapters, parse, Wilson-CI aggregation,
runner, CLI + Lambda handler) with a TDD test suite. See git history (`merge(m0): T01…T14`).

📄 **M1–M4 fully planned** — design spec + TDD task breakdown for every milestone
([`docs/tasks/`](docs/tasks/) — 93 task files total across M0–M4). Ready to hand to the orchestrator
milestone by milestone.

🔜 **Next:** implement **M1** (`docs/tasks/M1-README.md`), then M2 (dashboard goes live), M3, M4.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
