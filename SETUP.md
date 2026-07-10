# Setup & Running Guide

## Prerequisites

- **Python 3.13+**
- **PostgreSQL** (local, with `pgvector` extension for KB embeddings)
- **Node.js 18+** (for the dashboard frontend)
- **Git**

## 1. Clone & Install (Backend)

```bash
git clone https://github.com/shubham-mantri/gw-geo-stormbreaker.git
cd gw-geo-stormbreaker

# Install Python package in dev mode (includes all deps)
make install
# or: pip install -e ".[dev]"
```

## 2. Database Setup

```bash
# Create the Postgres database
createdb gw_geo

# Run migrations
alembic upgrade head
```

Default DB URL: `postgresql+psycopg://localhost/gw_geo` (override with `GEO_DATABASE_URL` in `.env`).

## 3. Environment Configuration

```bash
cp .env.example .env
```

Edit `.env` with your chosen LLM gateway. Available options:

### Option A: AWS Bedrock (recommended for production)

```env
GEO_LLM_GATEWAY=bedrock
GEO_BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-20250514
GEO_BEDROCK_REGION=us-east-1

# AWS credentials (via env vars, ~/.aws/credentials, or IAM role)
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret
# AWS_SESSION_TOKEN=...  (optional, for assumed roles)
```

### Option B: Local Claude CLI ($0 on Max subscription)

```env
GEO_LLM_GATEWAY=local_claude
# No API key needed — uses your local `claude` CLI subscription
# Optionally configure:
# GEO_CLAUDE_CLI_MODEL=sonnet
# GEO_CLAUDE_CLI_CONFIG_DIR=~/.asterisk/Work
```

### Option C: Portkey Gateway

```env
GEO_LLM_GATEWAY=portkey
GEO_PORTKEY_API_KEY=your-portkey-key
GEO_PORTKEY_CONFIG=your-config-id
```

### Option D: Direct Anthropic API

```env
GEO_LLM_GATEWAY=direct
GEO_ANTHROPIC_API_KEY=your-key
GEO_OPENAI_API_KEY=your-key  # for embeddings
```

### Engine API Keys (for measurement probes)

These are needed regardless of LLM gateway — they probe real AI engines:

```env
GEO_PERPLEXITY_API_KEY=your-key
GEO_OPENAI_API_KEY=your-key
GEO_ANTHROPIC_API_KEY=your-key
GEO_GEMINI_API_KEY=your-key
GEO_COPILOT_API_KEY=your-key
# GEO_DEEPSEEK_API_KEY=your-key
# GEO_DEEPSEEK_ENABLED=true  (off by default)
```

### Raw Archive (measurement payloads)

```env
# Local filesystem (no AWS S3 needed):
GEO_RAW_ARCHIVE_BACKEND=local
GEO_RAW_ARCHIVE_DIR=.raw_archive
```

## 4. Run the Backend API

```bash
uvicorn gw_geo.api.app:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

The API is now at `http://localhost:8000`. Health check: `GET /healthz`.

## 5. Run the Frontend Dashboard

```bash
cd web
npm install
npm run dev
```

Dashboard at `http://localhost:3000`. It proxies API calls to `:8000`.

## 6. CLI Commands

```bash
# Run a visibility measurement pass
python -m gw_geo.cli measure --brand <brand-id> --engines perplexity,openai --n 8

# Train ranking models
python -m gw_geo.cli rank --input ranking_data.json

# Generate opportunity queue
python -m gw_geo.cli opportunities --brand <brand-id>

# Run attribution reconciliation
python -m gw_geo.cli reconcile --brand <brand-id>

# Full measure-sense-adapt cycle
python -m gw_geo.cli adapt --brand <brand-id>

# Login to browser surfaces (ChatGPT/Grok/Google AI — one-time)
python -m gw_geo.cli login --surface chatgpt
```

## 7. Running Tests

```bash
# Full suite (hermetic, no live API calls)
make test
# or: pytest -m "not live" -q

# Lint + type check + tests
make check

# Run only Bedrock integration tests
pytest tests/content/test_llm_bedrock.py tests/content/test_gateway_bedrock.py -v
```

## 8. Browser Capture (Optional)

For measuring consumer-only surfaces (Google AI Overviews, ChatGPT web, Grok):

```bash
# Install Playwright browsers (one-time)
playwright install chromium

# Configure local capture
echo 'GEO_CAPTURE_BACKEND=local' >> .env
echo 'GEO_LOCAL_BROWSER_PROFILE_DIR=/path/to/chrome/profile' >> .env

# Login to surfaces (opens a browser window)
python -m gw_geo.cli login --surface chatgpt
python -m gw_geo.cli login --surface google
python -m gw_geo.cli login --surface grok
```

## Architecture Overview

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Next.js    │────▶│  FastAPI     │────▶│  PostgreSQL      │
│  Dashboard  │     │  :8000       │     │  + pgvector      │
│  :3000      │     └──────┬───────┘     └──────────────────┘
└─────────────┘            │
                           │ LLM Gateway (GEO_LLM_GATEWAY)
                           ▼
              ┌────────────────────────────┐
              │  bedrock / local_claude /   │
              │  portkey / direct           │
              └────────────────────────────┘
```

All internal LLM work (content generation, answer extraction, guardrails, onboarding suggestions) routes through the configured gateway. Measurement probes always hit real engine APIs directly.
