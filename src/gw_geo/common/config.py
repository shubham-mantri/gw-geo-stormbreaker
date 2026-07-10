"""Typed, env-driven settings for DB, S3, engine API keys, and default sampling params."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The insecure development defaults that must never reach production. Kept as the single source of
# truth for both the defaults below and the production fail-fast guard.
# >=32 bytes so PyJWT doesn't warn about HMAC-SHA256 key length (RFC 7518 §3.2); still an obvious
# dev placeholder that the production guard below rejects. Real deployments set GEO_JWT_SECRET.
_DEV_JWT_SECRET = "dev-insecure-change-me-set-GEO_JWT_SECRET-before-any-non-local-use"
_DEV_PIXEL_SALT = "dev-salt"
_PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")
    # Deployment environment; anything outside `_PRODUCTION_ENVIRONMENTS` is treated as non-prod
    # (dev/test/CI/e2e), where the insecure defaults below are tolerated.
    environment: str = "development"
    database_url: str = "postgresql+psycopg://localhost/geo_dev"
    s3_bucket: str = "gw-geo-dev"
    aws_region: str = "us-east-1"

    # Raw-payload archive backend (measurement.runner.RawArchive). Default "s3" (S3RawArchive);
    # set to "local" for a local-only run (no AWS) -- build_runtime then uses a LocalFileArchive
    # rooted at `raw_archive_dir` instead of hitting S3.
    raw_archive_backend: str = "s3"          # "s3" | "local"
    raw_archive_dir: str = ""                # base dir for the "local" backend
    perplexity_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    default_n_samples: int = 8
    default_geos: list[str] = ["us"]
    max_probe_concurrency: int = 8

    # M1 API engine keys
    gemini_api_key: str = ""
    copilot_api_key: str = ""
    deepseek_api_key: str = ""
    deepseek_enabled: bool = False          # TRD OT3 — off by default

    # M1 capture-fleet config refs (values resolved from SSM/secret store at deploy)
    proxy_pool_config_ref: str = ""         # e.g. SSM path / secret name
    account_pool_config_ref: str = ""
    playwright_headless: bool = True

    # M5 LOCAL browser capture (no proxies/cloud/SSM). `capture_backend` selects the CaptureClient
    # `build_runtime` wires for the Playwright surfaces (google_ai_overviews/chatgpt/grok):
    #   "none"  -> build NO capturer (DEFAULT): those three surfaces are skipped, so the hermetic
    #              suite + any API-only run never launch a browser.
    #   "local" -> `capture.local.LocalCaptureClient` over a persistent Chrome/Chromium profile at
    #              `local_browser_profile_dir` (the user's OWN logins; run `cli login` once). No
    #              proxy/account pool -- fetches serialize behind one browser.
    #   "live"  -> the M1 proxy+account fleet (unchanged; still needs the pool refs above + a
    #              SecretProvider, and is otherwise reached via those refs).
    # An injected `build_runtime(..., capture=...)` (tests) always overrides this.
    capture_backend: str = "none"           # "none" | "local" | "live"
    local_browser_profile_dir: str = ""     # persistent user-data-dir the user logs in to once
    local_browser_channel: str = "chrome"   # Playwright channel: "chrome"|"msedge"|"" (bundled)

    # M1 drift canary
    drift_threshold: float = 0.2
    drift_sns_topic_arn: str = ""

    # M2 JWT / auth
    jwt_secret: str = _DEV_JWT_SECRET
    jwt_access_ttl_s: int = 900
    jwt_refresh_ttl_s: int = 1209600          # 14d

    # M2 API
    cors_allow_origins: list[str] = ["http://localhost:3000"]

    # M2 integrations (secrets via env/SSM; blank default = "not configured")
    hubspot_client_id: str = ""
    hubspot_client_secret: str = ""
    salesforce_client_id: str = ""
    salesforce_client_secret: str = ""
    ga4_property_id: str = ""
    ga4_credentials_ref: str = ""             # SSM/secret ref, never inline creds

    # M2 lead-capture pixel
    pixel_write_key_salt: str = _DEV_PIXEL_SALT

    # W4 local pixel serving (no CDN). The pixel SDK is built from `web/pixel/gwgeo.ts` to
    # `web/public/gwgeo.js` (`npm --prefix web run build:pixel`) and served by the LOCAL backend at
    # `GET /pixel/gwgeo.js` (see `api/routers/pixel.py`) -- never a CDN. `pixel_js_path` overrides
    # where that route reads the built bundle from; empty = the in-repo `web/public/gwgeo.js`
    # (resolved relative to the package, see `api/routers/pixel.py`). `pixel_url` / `pixel_api_base`
    # are what `GET /lead-capture/snippet` bakes into the install `<script>` tag: `pixel_url` is the
    # absolute URL the tag's `src` points at (the local bundle above), and `pixel_api_base` is the
    # origin the pixel beacons `POST /lead-capture/collect` to (emitted as the tag's `data-api`).
    # Both default to the conventional local uvicorn origin and are env-overridable per deployment.
    pixel_js_path: str = ""
    pixel_url: str = "http://localhost:8000/pixel/gwgeo.js"
    pixel_api_base: str = "http://localhost:8000"

    # M3 vector store + embeddings (TRD §2, OT4)
    vector_store: str = "pinecone"            # "pinecone" | "pgvector"
    pinecone_api_key: str = ""
    pinecone_index: str = "gw-geo-kb"
    embedding_model: str = "text-embedding-3-large"

    # LLM gateway for the content-chat path (generation, seeding briefs, competitor suggestion,
    # claim extraction, brand-voice scoring). `"local_claude"` (default) runs those calls through
    # the local `claude -p` CLI on the user's Claude Max subscription -- $0 API cost, no key needed
    # (see `content.llm_local.LocalClaudeCliClient` + the `claude_cli_*` settings below).
    # `"portkey"` routes through the Portkey gateway (needs `portkey_api_key`; provider routing /
    # virtual keys live in the dashboard Config, not in code). `"direct"` hits the providers
    # directly via the per-provider keys above (`anthropic_api_key` / `openai_api_key`).
    # `"bedrock"` routes through AWS Bedrock's Converse API (auth via AWS credentials / IAM role).
    # Embeddings are never served by local Claude -- they always fall to Portkey (when keyed) or
    # direct OpenAI.
    llm_gateway: str = "local_claude"         # "local_claude" | "portkey" | "direct" | "bedrock"
    portkey_api_key: str = ""
    portkey_base_url: str = "https://api.portkey.ai/v1"
    portkey_config: str = "pc-portke-0dd3de"  # dashboard Config id holding the provider virtual keys

    # AWS Bedrock (llm_gateway="bedrock"): auth via standard AWS credentials (env vars, IAM role,
    # or instance profile). `bedrock_model_id` is the full Bedrock model ID; `bedrock_region`
    # overrides `aws_region` for the Bedrock runtime client (useful when model access is in a
    # different region from S3/other AWS services).
    bedrock_model_id: str = "us.anthropic.claude-sonnet-4-20250514"
    bedrock_region: str = ""                  # falls back to `aws_region` when empty

    # Local Claude CLI (llm_gateway="local_claude"): how `LocalClaudeCliClient` invokes `claude -p`.
    # `claude_cli_config_dir` selects the Claude Max profile (expanduser'd at use); no API key.
    claude_cli_bin: str = "claude"
    claude_cli_config_dir: str = "~/.asterisk/Work"
    claude_cli_model: str = "sonnet"
    claude_cli_timeout_s: float = 300.0

    # M3 ranking (TRD §8)
    ranking_model_type: str = "gbt"           # "gbt" | "logreg"

    # M3 guardrail thresholds (fail-closed defaults; PRD §6.4)
    originality_threshold: float = 0.25       # max allowed shingle Jaccard vs corpus
    claim_sim_threshold: float = 0.8          # min KB support for a claim to be "verified"
    brand_voice_min: float = 0.7              # min brand-voice conformance score

    # M3 publishing connectors (PRD §6.4)
    wordpress_base_url: str = ""
    wordpress_token: str = ""
    webflow_token: str = ""
    webflow_site_id: str = ""
    framer_token: str = ""
    headless_publish_url: str = ""
    hosted_subdomain_base: str = "kb.example.com"

    # M4 off-site seeding channels (design §10; PRD §6.5)
    seeding_channels_enabled: list[str] = [
        "reddit", "quora", "g2", "capterra", "listicle", "wikipedia", "pr_wire", "expert_byline",
    ]

    # M4 bandit effort allocation (design §6.3/§8)
    bandit_policy: str = "ucb1"               # "ucb1" | "thompson"
    bandit_explore_c: float = 1.0

    # M4 self-adaptation: retrain trigger on drift breach (reuses `drift_threshold` above, M1)
    retrain_on_breach: bool = True

    # M4 RaaS pricing (design §9; PRD §9); no secrets here -- billing credentials stay in env/SSM
    raas_enabled: bool = False
    raas_basis: str = "per_lead"              # "per_lead" | "pct_pipeline"
    raas_rate: float = 0.0

    @model_validator(mode="after")
    def _forbid_insecure_defaults_in_production(self) -> "Settings":
        """Fail fast at construction if a production deployment still carries a dev-default secret.

        Shipping the well-known dev JWT secret or pixel salt to production would let anyone forge
        tokens / write-keys, so it must be a hard startup error -- not a silent runtime foot-gun.
        Gated strictly on `environment` so dev/test/CI/e2e (which use the defaults) are unaffected.
        """
        if self.environment.lower() in _PRODUCTION_ENVIRONMENTS:
            insecure = []
            if self.jwt_secret == _DEV_JWT_SECRET:
                insecure.append("GEO_JWT_SECRET")
            if self.pixel_write_key_salt == _DEV_PIXEL_SALT:
                insecure.append("GEO_PIXEL_WRITE_KEY_SALT")
            if insecure:
                raise ValueError(
                    f"insecure development default(s) in production for: {', '.join(insecure)}. "
                    "Set a real secret via the environment before deploying."
                )
        return self


@lru_cache
def get_settings() -> "Settings":
    return Settings()
