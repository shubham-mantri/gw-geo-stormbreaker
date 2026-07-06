"""Originality / plagiarism guardrail (PRD §1.2, §13) — the Athena fix.

Athena's documented failure was publishing content that turned out to be a near-duplicate of an
existing page. This guardrail is the specific check that would have caught it: k-word shingling +
Jaccard similarity of a draft against the closest documents an injected `CorpusSearch` (web/corpus
search) can find. Content is "original" iff `max_similarity` is **strictly below**
`originality_threshold` (config default 0.25) -- fail-closed, so a tie at the threshold blocks
rather than passes.

`CorpusSearch` is a Protocol so tests inject in-memory fakes (no live search calls, ever, in the
default suite). `WebCorpusSearch` below is a real implementation for production wiring, exercised
in tests only against a mocked `httpx` transport (`respx`), never live.

**Honesty note (M5 review): this guardrail is NOT enforced in the LOCAL-only default wiring.** A
real plagiarism corpus needs a web-search source this build deliberately does not have (LOCAL-only,
no SERP), so `api/wiring.py` injects a `_NoCorpus` stub whose `search` returns `[]`. With no
documents to compare against, `check_originality` returns `max_similarity=0.0` and `ok=True` for
*any* draft -- so a plagiarized draft passes this leg. That gap is made visible rather than silent:
`build_content_service` logs a warning and stamps `originality_enforced=False` onto every
`GuardrailReport` (see `GuardrailReport.originality_enforced`). It does **not** relax the gates that
actually block publish -- human `editor+` approval and KB claim-grounding (`verify_claims`) do that.
Enforcement returns automatically once a real `CorpusSearch` is wired.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

import httpx

_WORD_RE = re.compile(r"[a-z0-9]+")


class CorpusSearch(Protocol):
    """Injected corpus/web search: find the documents closest to a piece of text."""

    def search(self, text: str, *, top_k: int = 5) -> list[tuple[str, str]]:
        """Return up to `top_k` `(url, snippet)` pairs of the closest existing documents."""
        ...


def shingles(text: str, k: int = 5) -> set[str]:
    """Return the set of normalized k-word shingles in `text`.

    `text` is lowercased and tokenized on runs of alphanumerics (punctuation/whitespace dropped),
    so two texts differing only in case or punctuation shingle identically. Returns an empty set
    when `text` has fewer than `k` words -- too short to form even one shingle.
    """
    words = _WORD_RE.findall(text.lower())
    if len(words) < k:
        return set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity `|a∩b| / |a∪b|`; `0.0` if both sets are empty."""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_originality(
    draft_text: str, *, corpus: CorpusSearch, threshold: float = 0.25
) -> tuple[bool, float, list[str]]:
    """Check `draft_text` for plagiarism against the nearest documents `corpus` can find.

    Shingles the draft once, then compares it against the shingles of every `(url, snippet)`
    result `corpus.search(draft_text)` returns, via `jaccard`. The overall similarity is the max
    across all hits (the single closest existing document is what matters).

    Fail-closed: `ok` requires `max_similarity` **strictly less than** `threshold` -- a similarity
    equal to the threshold is still flagged, not passed.

    Returns:
        `(ok, max_similarity, matched_urls)`. `matched_urls` lists the URL of every corpus hit
        whose similarity is at or above `threshold` (the source(s) responsible for the flag),
        in the order `corpus.search` returned them. Empty when nothing meets/exceeds `threshold`,
        including when the corpus returns no hits at all.
    """
    draft_shingles = shingles(draft_text)

    max_similarity = 0.0
    matched_urls: list[str] = []
    for url, snippet in corpus.search(draft_text):
        similarity = jaccard(draft_shingles, shingles(snippet))
        max_similarity = max(max_similarity, similarity)
        if similarity >= threshold:
            matched_urls.append(url)

    return max_similarity < threshold, max_similarity, matched_urls


class WebCorpusSearch:
    """`CorpusSearch` backed by a real web/corpus search API, via an injected `httpx.Client`.

    Not exercised against a live endpoint anywhere in the default (`not live`) test suite -- tests
    inject a `respx`-mocked `httpx.Client`. `check_originality` itself only ever depends on the
    `CorpusSearch` Protocol, so production callers inject this while tests inject simple in-memory
    fakes (see `tests/content/guardrails/test_originality.py`).
    """

    def __init__(self, client: httpx.Client, *, endpoint: str, api_key: str) -> None:
        self._client = client
        self._endpoint = endpoint
        self._api_key = api_key

    def search(self, text: str, *, top_k: int = 5) -> list[tuple[str, str]]:
        """Query the search API for the `top_k` documents closest to `text`.

        Expects a JSON body shaped `{"results": [{"url": ..., "snippet": ...}, ...]}`.

        Raises:
            httpx.HTTPStatusError: the search API returned a non-2xx response.
        """
        response = self._client.get(
            self._endpoint,
            params={"q": text, "top_k": top_k},
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        results = payload.get("results", [])[:top_k]
        return [(item["url"], item["snippet"]) for item in results]
