# M3-T04 — Feature extraction (content → FeatureVector)

**Depends on:** T03 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** Extract the interpretable content features that predict citation (PRD §6.3, TRD §8):
structure, info-density, freshness, domain authority, corroboration count, embedding similarity, plus
format signals. Pure-Python heuristics + one **injected** `EmbeddingClient` (no live calls).

**Files:**
- Create: `src/gw_geo/ranking/features.py`
- Test: `tests/ranking/test_features.py`, `tests/ranking/__init__.py`

## Interface

```python
from typing import Protocol
from gw_geo.common.models import FeatureVector

class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]: ...

def cosine(a: list[float], b: list[float]) -> float: ...      # 0.0 if either is zero-vector
def info_density(text: str) -> float: ...                     # numeric/stat tokens per 100 words
def structure_score(content: str) -> float: ...              # 0..1: heading, list, table, definition-first
def freshness_days(published_at: str | None, now: str) -> float | None: ...

def extract_features(*, content: str, prompt_text: str, domain_authority: float,
                     corroboration_count: int, published_at: str | None,
                     embedder: EmbeddingClient, now: str) -> FeatureVector: ...
```

Rules: `has_schema` = content contains JSON-LD / `application/ld+json`; `has_faq` = an FAQ/Q&A heading
or `FAQPage`; `table_count` = number of markdown/HTML tables; `embedding_similarity =
cosine(embed(content), embed(prompt_text))`.

## Steps
- [ ] **1. Failing test** `tests/ranking/test_features.py`:

```python
from gw_geo.ranking.features import (info_density, structure_score, freshness_days,
                                     cosine, extract_features)

class StubEmbedder:
    # deterministic: vector keyed on presence of the word "crm"
    def embed(self, text):
        return [1.0, 0.0] if "crm" in text.lower() else [0.0, 1.0]

def test_info_density_counts_stats_per_100_words():
    text = "Our tool cut costs by 40% and saved 12 hours " + "word " * 92
    assert info_density(text) > 0  # 2 numeric tokens in ~100 words

def test_structure_score_rewards_structure():
    plain = "just a paragraph of prose with no structure at all"
    rich = "## What is X\nX is Y.\n- a\n- b\n\n| col | col |\n|--|--|\n| 1 | 2 |"
    assert structure_score(rich) > structure_score(plain)

def test_freshness_days():
    assert freshness_days("2026-06-22", "2026-07-02") == 10.0
    assert freshness_days(None, "2026-07-02") is None

def test_cosine_orthogonal_and_parallel():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

def test_extract_features_builds_vector():
    fv = extract_features(content='CRM guide <script type="application/ld+json">{}</script>',
                          prompt_text="best crm", domain_authority=0.7, corroboration_count=3,
                          published_at="2026-06-30", embedder=StubEmbedder(), now="2026-07-02")
    assert fv.has_schema is True
    assert fv.embedding_similarity == 1.0   # both contain "crm"
    assert fv.domain_authority == 0.7 and fv.corroboration_count == 3
    assert fv.freshness_days == 2.0
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `features.py`. Keep heuristics simple, deterministic, and documented; the
  `EmbeddingClient` is always injected (real impl uses the configured embedding model — not tested live).
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(ranking): content feature extraction`

## Acceptance
- `extract_features` returns a fully-populated `FeatureVector`; each heuristic unit-tested on fixed
  strings; embedding similarity via injected client; no live calls.
