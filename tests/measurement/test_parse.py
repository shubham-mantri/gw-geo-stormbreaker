import json
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import respx

from gw_geo.common.models import ProbeResult, Brand, Sentiment, SourceType
from gw_geo.measurement.parse import (
    ClaudeExtractor,
    LLMExtractor,
    _EXTRACTION_SYSTEM_PROMPT,
    _build_extraction_prompt,
    _extraction_schema,
    classify_source,
    normalize_url,
    parse,
)


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


# --- Additional coverage beyond the spec's verbatim tests -----------------------------------

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "answers"


def test_normalize_url_preserves_non_utm_query_and_strips_trailing_slash():
    assert normalize_url("https://Foo.com/path/?q=1&utm_campaign=x") == "https://foo.com/path?q=1"
    assert normalize_url("https://foo.com") == "https://foo.com"


def test_domain_of_strips_www():
    from gw_geo.measurement.parse import domain_of

    assert domain_of("https://www.g2.com/products/foo") == "g2.com"
    assert domain_of("foo.com") == "foo.com"


def test_classify_source_review_sites_and_default():
    assert classify_source("https://www.g2.com/products/foo/reviews") == SourceType.REVIEW_SITE
    assert classify_source("https://www.capterra.com/p/foo") == SourceType.REVIEW_SITE
    assert classify_source("https://example-blog.com/post") == SourceType.OTHER


def test_parse_tags_own_site_and_normalizes_cited_urls():
    data = json.loads((FIXTURES_DIR / "perplexity_sample.json").read_text())
    result = ProbeResult(**data)
    brand = Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com")

    extraction = parse(result, brand, StubExtractor(), probe_run_id="pr-fixture")

    assert extraction.probe_run_id == "pr-fixture"
    assert extraction.cited_urls == [normalize_url(u) for u in result.cited_urls]
    assert len(extraction.source_types) == len(extraction.cited_urls)
    assert SourceType.OWN_SITE in extraction.source_types
    assert SourceType.WIKIPEDIA in extraction.source_types
    assert SourceType.REDDIT in extraction.source_types
    assert SourceType.REVIEW_SITE in extraction.source_types
    # utm_source must be stripped from the G2 citation
    assert not any("utm_" in u for u in extraction.cited_urls)


# --- M5: extractor routed through the GEO_LLM_GATEWAY LLMClient (LLMExtractor) ----------------

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class _RecordingLLM:
    """Minimal `content.generate.LLMClient` recording its call and returning a canned dict.

    Mirrors `tests/content/test_gateway.py::_StubLLM` -- lets the hermetic suite drive
    `LLMExtractor` with no live `claude`/network call, whatever backend it would wrap in prod.
    """

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, *, system: str, prompt: str, schema: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        return self._result


def test_llm_extractor_returns_extraction_shape_and_reuses_shared_prompt_and_schema():
    """`LLMExtractor` builds the shared system+prompt+schema and returns the extraction dict.

    Passing the same reply through the injected `LLMClient`, the result must (a) match the
    extraction dict shape, (b) validate against `_extraction_schema()`, and (c) prove the two
    extractors can't drift: it calls `complete` with exactly the shared system prompt, the shared
    user-prompt builder's output, and the shared schema.
    """
    brand = Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com", competitors=["Acme"])
    answer = "Foo is the best CRM; Acme trails."
    canned = {
        "brand_mentioned": True,
        "position": 1,
        "sentiment": "positive",
        "competitors_present": ["Acme"],
    }
    llm = _RecordingLLM(canned)

    out = LLMExtractor(llm).extract(answer, brand)

    assert out == canned
    jsonschema.validate(out, _extraction_schema())  # the returned dict passes the schema
    call = llm.calls[0]
    assert call["system"] == _EXTRACTION_SYSTEM_PROMPT
    assert call["prompt"] == _build_extraction_prompt(answer, brand)
    assert call["schema"] == _extraction_schema()


@respx.mock
def test_claude_and_llm_extractor_agree_from_the_same_reply():
    """Same reply -> identical dict from both backends: downstream can't tell them apart.

    `ClaudeExtractor` reads it from the mocked Anthropic tool_use block; `LLMExtractor` reads it
    from an injected `LLMClient`. Both must return the same extraction dict.
    """
    brand = Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com", competitors=["Acme"])
    answer = "Foo leads the market; Acme is second."
    reply = {
        "brand_mentioned": True,
        "position": 1,
        "sentiment": "comparison",
        "competitors_present": ["Acme"],
    }
    respx.post(_ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200,
            json={"content": [{"type": "tool_use", "name": "record_extraction", "input": reply}]},
        )
    )

    claude_out = ClaudeExtractor(api_key="ak").extract(answer, brand)
    llm_out = LLMExtractor(_RecordingLLM(reply)).extract(answer, brand)

    assert claude_out == llm_out == reply


@respx.mock
def test_claude_extractor_direct_wire_body_unchanged():
    """The `direct` path is preserved byte-for-byte: no system turn, same tool/schema/prompt.

    Locks the "do not regress the direct Messages-API path" guarantee AND proves the shared
    helpers reproduce exactly what `ClaudeExtractor` sends on the wire (`record_extraction` tool,
    the shared `_extraction_schema()`, the shared user prompt, opus model, no `system` field).
    """
    brand = Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com", competitors=["Acme"])
    answer = "Foo is great."
    route = respx.post(_ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "name": "record_extraction",
                        "input": {
                            "brand_mentioned": True,
                            "position": None,
                            "sentiment": "positive",
                            "competitors_present": [],
                        },
                    }
                ]
            },
        )
    )

    ClaudeExtractor(api_key="ak").extract(answer, brand)

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "claude-opus-4-8"  # opus default preserved
    assert "system" not in body  # direct path sends no system turn (byte-for-byte)
    assert body["tool_choice"] == {"type": "tool", "name": "record_extraction"}
    assert body["tools"][0]["name"] == "record_extraction"
    assert body["tools"][0]["input_schema"] == _extraction_schema()
    assert body["messages"] == [{"role": "user", "content": _build_extraction_prompt(answer, brand)}]
    assert route.calls.last.request.headers["x-api-key"] == "ak"
