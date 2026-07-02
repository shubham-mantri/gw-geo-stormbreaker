# M3-T08 — Guardrail: brand-voice conformance

**Depends on:** T03 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** Score a draft's conformance to the brand voice profile (PRD §6.4 guardrails). An **injected**
`VoiceScorer` (real impl = LLM rubric) returns a score + violations; content is on-voice iff
`score ≥ brand_voice_min` (config, default 0.7). No live LLM calls in tests.

**Files:**
- Create: `src/gw_geo/content/guardrails/brand_voice.py`
- Test: `tests/content/guardrails/test_brand_voice.py`

## Interface

```python
from typing import Any, Protocol

class VoiceScorer(Protocol):
    def score(self, text: str, voice_profile: dict[str, Any]) -> dict[str, Any]: ...
    # returns {"score": float (0..1), "violations": list[str]}

def check_brand_voice(draft_text: str, voice_profile: dict[str, Any], *, scorer: VoiceScorer,
                      min_score: float = 0.7) -> tuple[bool, float, list[str]]: ...
# returns (ok, score, violations);  ok = score >= min_score
```

## Steps
- [ ] **1. Failing test** `tests/content/guardrails/test_brand_voice.py`:

```python
from gw_geo.content.guardrails.brand_voice import check_brand_voice

class StubScorer:
    def __init__(self, score, violations=()):
        self._score = score; self._v = list(violations)
    def score(self, text, voice_profile):
        return {"score": self._score, "violations": self._v}

PROFILE = {"tone": "confident, plain-spoken", "banned": ["synergy", "leverage"]}

def test_on_voice_passes():
    ok, score, viol = check_brand_voice("clear helpful copy", PROFILE,
                                        scorer=StubScorer(0.9), min_score=0.7)
    assert ok is True and score == 0.9 and viol == []

def test_off_voice_fails_with_violations():
    ok, score, viol = check_brand_voice("let us leverage synergy", PROFILE,
                                        scorer=StubScorer(0.4, ["banned term: synergy"]), min_score=0.7)
    assert ok is False and score == 0.4 and "banned term: synergy" in viol

def test_boundary_is_inclusive():
    ok, _, _ = check_brand_voice("x", PROFILE, scorer=StubScorer(0.7), min_score=0.7)
    assert ok is True
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `brand_voice.py`. Provide a real `LLMVoiceScorer` (injected LLM client, JSON
  rubric) — **not** tested live.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(content): brand-voice conformance guardrail`

## Acceptance
- On-voice text passes; off-voice text fails and surfaces violations; boundary inclusive; threshold
  configurable; no live LLM calls.
