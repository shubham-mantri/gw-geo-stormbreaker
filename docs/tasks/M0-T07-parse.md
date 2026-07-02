# M0-T07 — Parse (answer → extraction)

**Depends on:** T02 · **Wave:** 1 · **Suggested agent:** general-purpose

**Goal:** Turn a `ProbeResult` into an `AnswerExtraction` (mention/position/sentiment/citations
+ source-type tags). URL normalization is pure-Python and independently tested; sentiment/mention
uses an **injected** LLM extractor (no live calls in tests).

**Files:**
- Create: `src/gw_geo/measurement/parse.py`
- Test: `tests/measurement/test_parse.py`, `tests/fixtures/answers/perplexity_sample.json`

## Interface

```python
from typing import Protocol
from gw_geo.common.models import ProbeResult, AnswerExtraction, Brand, SourceType

def normalize_url(url: str) -> str: ...          # strip utm/#, lower host, no trailing slash
def domain_of(url: str) -> str: ...              # registrable host
def classify_source(url: str) -> SourceType: ... # reddit.com→REDDIT, *.wikipedia.org→WIKIPEDIA,
                                                 # g2.com/capterra→REVIEW_SITE, own domain→OWN_SITE...

class Extractor(Protocol):
    def extract(self, answer_text: str, brand: Brand) -> dict: ...
    # returns {"brand_mentioned": bool, "position": int|None, "sentiment": str,
    #          "competitors_present": [str]}

def parse(result: ProbeResult, brand: Brand, extractor: Extractor,
          probe_run_id: str) -> AnswerExtraction: ...
```

## Steps
- [ ] **1. Failing tests** `tests/measurement/test_parse.py`:

```python
from gw_geo.common.models import ProbeResult, Brand, Sentiment, SourceType
from gw_geo.measurement.parse import normalize_url, classify_source, parse

def test_normalize_strips_tracking():
    assert normalize_url("https://X.com/a/?utm_source=z#h") == "https://x.com/a"

def test_classify_source():
    assert classify_source("https://www.reddit.com/r/x") == SourceType.REDDIT
    assert classify_source("https://en.wikipedia.org/wiki/Y") == SourceType.WIKIPEDIA

class StubExtractor:
    def extract(self, answer_text, brand):
        return {"brand_mentioned": True, "position": 2, "sentiment": "positive",
                "competitors_present": ["Acme"]}

def test_parse_builds_extraction():
    r = ProbeResult(engine="perplexity", answer_text="...", cited_urls=["https://reddit.com/r/x"])
    e = parse(r, Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com"),
              StubExtractor(), probe_run_id="pr1")
    assert e.brand_mentioned and e.position == 2
    assert e.sentiment == Sentiment.POSITIVE
    assert SourceType.REDDIT in e.source_types
```

- [ ] **2. Run → fail.**
- [ ] **3. Implement** `parse.py`. Real `Extractor` impl (Claude JSON mode) also lives here but is
  NOT unit-tested with live calls — tests use `StubExtractor`. Add the fixture file.
- [ ] **4. Run → pass**; mypy clean.
- [ ] **5. Commit:** `feat(measurement): answer parser + url/source classification`

## Acceptance
- `normalize_url`/`domain_of`/`classify_source` unit-tested; `parse()` maps extractor output +
  cited URLs into a valid `AnswerExtraction` with source-type tags.
