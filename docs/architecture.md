# Architecture — gw-geo-stormbreaker

Companion to [`prd.md`](prd.md). Data-flow view of the seven subsystems.

```
                          ┌───────────────────────────────────────────┐
                          │            DATA PLATFORM (multi-tenant)     │
                          │  Postgres · S3 · Pinecone · SQS/queue       │
                          └───────────────────────────────────────────┘
   (1) MEASUREMENT        (2) ATTRIBUTION        (3) FEATURE/RANK ML
   Discover→Probe→Parse   citation→visitor→lead  what content gets cited
        │                        ▲                       │
        ▼                        │                       ▼
   (4) CONTENT ENGINE  ───────────────────────────►  (5) OFF-SITE SEEDING
   generate · ground · gate · publish               place on cited sources
        │                                                │
        └──────────────► (6) ORCHESTRATION / AGENTS ◄────┘
                          plan work, run the loop, retrain
                                     │
                                     ▼
                          (7) DASHBOARD / API / REPORTING
                          visibility · pipeline · alerts · exec view
```

## The core loop

`Discover → Recognize → Rank → Generate → Seed → Attribute → Re-learn`

1. **Discover** — build the prompt universe per brand (intent-clustered).
2. **Probe** — run each prompt across engines, sampling N× for non-determinism.
3. **Parse** — extract mention / position / sentiment / cited sources.
4. **Aggregate** — roll up to visibility metrics with confidence intervals.
5. **Rank** — learn per-engine "feature factors" that predict citation.
6. **Generate + Seed** — produce on-brand content, publish on-site, seed off-site.
7. **Attribute** — link citations to sessions, leads, pipeline.
8. **Re-learn** — drift canary detects engine changes → retrain.

## Key architectural rules

- **Engine adapter isolation:** each engine sits behind a stable adapter interface.
  Adding an engine = one new adapter, no core changes. Engines change often; isolate churn.
- **Probabilities, not point observations:** every visibility metric carries a sample
  count and confidence interval. Never treat a single answer as ground truth.
- **Cost governor from day one:** probing dominates cost. Per-tenant sampling budgets.
- **Human approval gate** before any content is published or seeded (enterprise + brand safety).
- **White-hat only:** no hidden-text/prompt-injection/cloaking. Compliance rules engine on seeding.

## Stack (self-contained)

Standalone, no external/shared services: Python 3.13 · async workers (Lambda or containers) ·
PostgreSQL (system of record) · vector store (pgvector or Pinecone) · S3-compatible object
storage · self-contained auth (JWT + lightweight RBAC, or a hosted provider) · Next.js/React
dashboard (see [`ui-spec.md`](ui-spec.md)).

See [`trd.md`](trd.md) for the technical design and interface contracts.
