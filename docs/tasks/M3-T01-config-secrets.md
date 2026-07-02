# M3-T01 — Config & secrets (ranking, KB, guardrails, publishing)

**Depends on:** M0 config · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** Extend `Settings` (M0 `common/config.py`) with the config M3 needs: vector store +
embedding/model selection, guardrail thresholds, and publishing-connector credentials. All secrets
come from env/SSM (never in repo); everything typed and defaulted so tests run without a `.env`.

**Files:**
- Edit: `src/gw_geo/common/config.py`
- Test: `tests/common/test_config.py` (extend)

## Interface

```python
class Settings(BaseSettings):
    # ... existing M0 fields (database_url, s3_bucket, *_api_key, default_n_samples, ...) ...
    # vector store + embeddings (TRD §2, OT4)
    vector_store: str = "pinecone"           # "pinecone" | "pgvector"
    pinecone_api_key: str = ""
    pinecone_index: str = "gw-geo-kb"
    embedding_model: str = "text-embedding-3-large"
    # ranking (TRD §8)
    ranking_model_type: str = "gbt"          # "gbt" | "logreg"
    # guardrail thresholds (fail-closed defaults; PRD §6.4)
    originality_threshold: float = 0.25      # max allowed shingle Jaccard vs corpus
    claim_sim_threshold: float = 0.8         # min KB support for a claim to be "verified"
    brand_voice_min: float = 0.7             # min brand-voice conformance score
    # publishing connectors (PRD §6.4)
    wordpress_base_url: str = ""
    wordpress_token: str = ""
    webflow_token: str = ""
    webflow_site_id: str = ""
    framer_token: str = ""
    headless_publish_url: str = ""
    hosted_subdomain_base: str = "kb.example.com"
```

## Steps
- [ ] **1. Failing test** `tests/common/test_config.py` (append):

```python
from gw_geo.common.config import Settings

def test_m3_defaults_present():
    s = Settings()
    assert s.vector_store == "pinecone"
    assert s.ranking_model_type in {"gbt", "logreg"}
    assert 0.0 < s.originality_threshold < 1.0
    assert s.claim_sim_threshold == 0.8
    assert s.brand_voice_min == 0.7
    assert s.hosted_subdomain_base.endswith(".com")

def test_m3_env_override(monkeypatch):
    monkeypatch.setenv("GEO_RANKING_MODEL_TYPE", "logreg")
    monkeypatch.setenv("GEO_ORIGINALITY_THRESHOLD", "0.15")
    s = Settings()
    assert s.ranking_model_type == "logreg"
    assert s.originality_threshold == 0.15
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the new fields on `Settings` (keep the `GEO_` env prefix + `extra="ignore"`).
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(config): M3 ranking/kb/guardrail/publishing settings`

## Acceptance
- New typed settings exist with fail-closed defaults; env override works via the `GEO_` prefix; no
  secret values committed; mypy-strict clean.
