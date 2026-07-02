# M3-T07 — Guardrail: originality / plagiarism

**Depends on:** T03 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** The **plagiarism guard** — the specific check that prevents Athena's documented plagiarism
failure (PRD §1.2, §13). k-shingling + Jaccard similarity of a draft against an **injected**
`CorpusSearch` (web/corpus). Content is original iff `max_similarity < threshold` (config
`originality_threshold`, default 0.25; **fail-closed**). No live search in tests.

**Files:**
- Create: `src/gw_geo/content/guardrails/__init__.py`, `src/gw_geo/content/guardrails/originality.py`
- Test: `tests/content/guardrails/test_originality.py`, `tests/content/guardrails/__init__.py`

## Interface

```python
from typing import Protocol

class CorpusSearch(Protocol):
    def search(self, text: str, *, top_k: int = 5) -> list[tuple[str, str]]: ...
    # returns [(url, snippet), ...] of the closest existing documents

def shingles(text: str, k: int = 5) -> set[str]: ...          # k-word shingles, normalized
def jaccard(a: set[str], b: set[str]) -> float: ...           # |a∩b| / |a∪b|, 0 if both empty
def check_originality(draft_text: str, *, corpus: CorpusSearch,
                      threshold: float = 0.25) -> tuple[bool, float, list[str]]: ...
# returns (ok, max_similarity, matched_urls);  ok = max_similarity < threshold
```

## Steps
- [ ] **1. Failing test** `tests/content/guardrails/test_originality.py`:

```python
from gw_geo.content.guardrails.originality import shingles, jaccard, check_originality

def test_jaccard_bounds():
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a"}, {"a"}) == 1.0
    assert 0.0 < jaccard({"a", "b"}, {"b", "c"}) < 1.0

class PlagiarizingCorpus:
    def __init__(self, doc): self.doc = doc
    def search(self, text, *, top_k=5): return [("https://source.com/orig", self.doc)]

class EmptyCorpus:
    def search(self, text, *, top_k=5): return []

def test_near_duplicate_flagged():
    original = "the quick brown fox jumps over the lazy dog every single morning without fail"
    ok, sim, urls = check_originality(original, corpus=PlagiarizingCorpus(original), threshold=0.25)
    assert ok is False and sim > 0.25 and urls == ["https://source.com/orig"]

def test_original_passes():
    draft = "a totally unrelated sentence about distributed systems and consensus protocols here"
    ok, sim, urls = check_originality(draft, corpus=EmptyCorpus(), threshold=0.25)
    assert ok is True and sim == 0.0 and urls == []

def test_paraphrase_below_threshold_passes():
    a = "our platform helps growth teams measure ai search visibility across many engines daily"
    b = "consensus protocols coordinate replicas in distributed databases under network partitions"
    ok, sim, _ = check_originality(a, corpus=type("C", (), {"search": lambda s, t, **k: [("u", b)]})(),
                                   threshold=0.25)
    assert ok is True and sim < 0.25
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `originality.py`. `check_originality` shingles the draft, compares against each
  corpus snippet's shingles, takes the max Jaccard, returns `(max<threshold, max, urls_over_threshold)`.
  Provide a real `WebCorpusSearch` (injected httpx / search API) — **not** tested live.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(content): originality/plagiarism guardrail`

## Acceptance
- Near-duplicate text is flagged (`ok=False`) with the matched source URL; original/paraphrased text
  passes; threshold is configurable and fail-closed; no live search calls.
