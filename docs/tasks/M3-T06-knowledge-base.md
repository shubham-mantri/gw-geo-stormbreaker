# M3-T06 — Brand knowledge base (grounding, vector store)

**Depends on:** T03 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** The per-brand **source of truth** for generation (PRD §6.4): approved facts / USPs /
products / pricing / certifications / claims, indexed in a vector store for semantic grounding. This
is the substrate that prevents hallucination — `ground(query)` returns the supporting `Fact`s a claim
can be checked against (used by T15). `VectorStore` + `EmbeddingClient` are **injected** Protocols.

**Files:**
- Create: `src/gw_geo/content/kb.py`
- Test: `tests/content/test_kb.py`, `tests/content/__init__.py`

## Interface

```python
from typing import Any, Protocol
from gw_geo.common.models import Fact

class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]: ...

class VectorStore(Protocol):
    def upsert(self, id: str, vector: list[float], meta: dict[str, Any]) -> None: ...
    def query(self, vector: list[float], top_k: int) -> list[tuple[str, float, dict[str, Any]]]: ...
    # returns [(id, score, meta), ...] sorted by score desc

class KnowledgeBase:
    def __init__(self, *, brand_id: str, store: VectorStore, embedder: EmbeddingClient) -> None: ...
    def add_fact(self, fact: Fact) -> None: ...                 # embeds text, upserts with meta
    def ground(self, query: str, *, top_k: int = 5) -> list[Fact]: ...   # top-k supporting facts
```

## Steps
- [ ] **1. Failing test** `tests/content/test_kb.py` (in-memory fake store, deterministic embedder):

```python
from gw_geo.common.models import Fact
from gw_geo.content.kb import KnowledgeBase

class FakeStore:
    def __init__(self): self.rows = {}
    def upsert(self, id, vector, meta): self.rows[id] = (vector, meta)
    def query(self, vector, top_k):
        scored = [(i, sum(a*b for a, b in zip(vector, v)), m) for i, (v, m) in self.rows.items()]
        scored.sort(key=lambda r: r[1], reverse=True)
        return scored[:top_k]

class WordEmbedder:
    VOCAB = ["price", "uptime", "soc2"]
    def embed(self, text):
        t = text.lower(); return [1.0 if w in t else 0.0 for w in self.VOCAB]

def _kb():
    kb = KnowledgeBase(brand_id="b1", store=FakeStore(), embedder=WordEmbedder())
    kb.add_fact(Fact(id="f1", brand_id="b1", text="Plans start at $29/mo price", category="pricing"))
    kb.add_fact(Fact(id="f2", brand_id="b1", text="We are SOC2 Type II certified soc2", category="certification"))
    kb.add_fact(Fact(id="f3", brand_id="b1", text="99.99% uptime SLA uptime", category="claim"))
    return kb

def test_ground_returns_relevant_fact_first():
    kb = _kb()
    facts = kb.ground("what is your pricing price?", top_k=1)
    assert len(facts) == 1 and facts[0].id == "f1"

def test_ground_returns_facts_not_ids():
    kb = _kb()
    facts = kb.ground("uptime guarantee uptime", top_k=1)
    assert facts[0].category == "claim" and "uptime" in facts[0].text
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `kb.py`. `add_fact` embeds `fact.text` and upserts with the full `Fact` in
  `meta` (so `ground` can reconstruct `Fact` objects). Provide a real `PineconeVectorStore` /
  `PgVectorStore` stub class too (config-selected per T01) — **not** exercised in tests.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(content): brand knowledge base with vector grounding`

## Acceptance
- `add_fact` embeds+upserts; `ground` returns the top-k most relevant `Fact` objects via the injected
  store; no live embedding/vector calls in tests.
