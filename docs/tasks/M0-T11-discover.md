# M0-T11 — Discover (prompt universe builder)

**Depends on:** T02, T03 · **Wave:** 2 · **Suggested agent:** general-purpose

**Goal:** Build a `list[Prompt]` for a brand from seed topics via an **injected** LLM expander
(no live calls in tests). Intent clustering v0 = simple label from the expander; volume estimate
v0 = pluggable proxy (default 0.0). Real search-volume + embedding clustering is M1.

**Files:**
- Create: `src/gw_geo/measurement/discover.py`
- Test: `tests/measurement/test_discover.py`

## Interface

```python
from typing import Protocol
from gw_geo.common.models import Brand, Prompt

class PromptExpander(Protocol):
    def expand(self, brand: Brand, seed_topics: list[str], size: int) -> list[dict]: ...
    # each dict: {"text": str, "intent_cluster": str}

def build_prompt_set(brand: Brand, seed_topics: list[str], size: int,
                     expander: PromptExpander,
                     id_fn=None) -> list[Prompt]: ...
```

Rules: dedupe by normalized text; cap at `size`; assign `tenant_id`/`brand_id` from `brand`;
`id_fn` defaults to a uuid4 factory (inject in tests for determinism).

## Steps
- [ ] **1. Failing test** `tests/measurement/test_discover.py`:

```python
from itertools import count
from gw_geo.common.models import Brand
from gw_geo.measurement.discover import build_prompt_set

class StubExpander:
    def expand(self, brand, seed_topics, size):
        return [{"text": f"best {t} for smb?", "intent_cluster": "evaluation"}
                for t in seed_topics][:size]

def test_builds_and_caps():
    ids = count(); brand = Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com")
    prompts = build_prompt_set(brand, ["crm","helpdesk","erp"], size=2,
                               expander=StubExpander(), id_fn=lambda: f"p{next(ids)}")
    assert len(prompts) == 2
    assert prompts[0].brand_id == "b1" and prompts[0].tenant_id == "t1"
    assert prompts[0].intent_cluster == "evaluation"

def test_dedupes():
    brand = Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com")
    class Dup:
        def expand(self, b, s, n): return [{"text":"x","intent_cluster":"c"}]*3
    assert len(build_prompt_set(brand, ["a"], 10, Dup(), id_fn=lambda: "p")) == 1
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `discover.py`. Provide a real `LLMExpander` class here too (uses Claude),
  not unit-tested with live calls.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(measurement): prompt-universe discover module`

## Acceptance
- `build_prompt_set` returns deduped, capped `Prompt`s scoped to the brand/tenant with intent
  labels; deterministic under injected `id_fn`.
