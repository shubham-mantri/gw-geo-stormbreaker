# M4-T01 — Config & secrets (seeding · self-adaptation · RaaS)

**Depends on:** M0 config (`common/config.py`) · **Wave:** 0 · **Suggested agent:** general-purpose

**Goal:** Extend `Settings` with the M4 knobs — enabled seeding channels, bandit policy, retrain
toggle, and RaaS pricing flags — env-driven and typed (`pydantic-settings`). Secrets stay in env/SSM,
never in repo (design §5).

**Files:**
- Edit: `src/gw_geo/common/config.py`
- Test: `tests/common/test_config_m4.py`

## Interface

```python
# additions to Settings (pydantic-settings BaseSettings)
seeding_channels_enabled: list[str] = ["reddit", "quora", "g2", "capterra",
                                       "listicle", "wikipedia", "pr_wire", "expert_byline"]
bandit_policy: str = "ucb1"            # "ucb1" | "thompson"
bandit_explore_c: float = 1.0
retrain_on_breach: bool = True
raas_enabled: bool = False
raas_basis: str = "per_lead"           # "per_lead" | "pct_pipeline"
raas_rate: float = 0.0
# reuses existing M1 drift_threshold; no new secrets committed
```

## Steps
- [ ] **1. Failing test** `tests/common/test_config_m4.py`:

```python
import os
from gw_geo.common.config import Settings

def test_m4_defaults():
    s = Settings(_env_file=None)
    assert s.raas_enabled is False
    assert s.raas_basis == "per_lead"
    assert s.bandit_policy == "ucb1"
    assert "reddit" in s.seeding_channels_enabled and "wikipedia" in s.seeding_channels_enabled

def test_m4_env_override(monkeypatch):
    monkeypatch.setenv("RAAS_ENABLED", "true")
    monkeypatch.setenv("BANDIT_POLICY", "thompson")
    s = Settings(_env_file=None)
    assert s.raas_enabled is True and s.bandit_policy == "thompson"
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** the fields on `Settings` (defaults above). Keep existing M0/M1 fields intact.
- [ ] **4. Run → pass**; `mypy src/gw_geo/common` clean.
- [ ] **5. Commit:** `feat(config): M4 seeding/bandit/retrain/RaaS settings`

## Acceptance
- New settings exist with the exact names/defaults above, overridable via env; no secrets in repo;
  mypy-strict clean; M0/M1 settings unchanged.
