import httpx
import respx

from gw_geo.content.guardrails.originality import (
    WebCorpusSearch,
    check_originality,
    jaccard,
    shingles,
)


def test_jaccard_bounds():
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a"}, {"a"}) == 1.0
    assert 0.0 < jaccard({"a", "b"}, {"b", "c"}) < 1.0


class PlagiarizingCorpus:
    def __init__(self, doc):
        self.doc = doc

    def search(self, text, *, top_k=5):
        return [("https://source.com/orig", self.doc)]


class EmptyCorpus:
    def search(self, text, *, top_k=5):
        return []


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


# --- Additional coverage beyond the spec-mandated tests above ---------------------------------


def test_shingles_normalizes_case_and_punctuation():
    # Case and punctuation are not meaningful for plagiarism detection: "Fox." and "fox" must
    # shingle identically so a re-punctuated/re-cased copy is still caught.
    assert shingles("The Quick, Brown Fox!", k=3) == shingles("the quick brown fox", k=3)


def test_shingles_shorter_than_k_is_empty():
    assert shingles("too short", k=5) == set()


def test_jaccard_is_symmetric():
    a, b = {"a", "b", "c"}, {"b", "c", "d"}
    assert jaccard(a, b) == jaccard(b, a)


def test_fail_closed_at_exact_threshold():
    # Fail-closed: a similarity exactly AT the threshold must not pass (strict "<" only).
    doc = "the quick brown fox jumps over the lazy dog every single morning without fail"

    class ExactMatchCorpus:
        def search(self, text, *, top_k=5):
            return [("https://source.com/exact", doc)]

    # Identical text against itself has similarity 1.0; with threshold == 1.0 this is the
    # boundary case and must still be blocked (ok is False), not pass.
    ok, sim, urls = check_originality(doc, corpus=ExactMatchCorpus(), threshold=1.0)
    assert sim == 1.0
    assert ok is False
    assert urls == ["https://source.com/exact"]


def test_max_similarity_taken_across_multiple_hits():
    draft = "the quick brown fox jumps over the lazy dog every single morning without fail"
    near_dup = draft
    unrelated = "a totally unrelated sentence about distributed systems and consensus protocols"

    class MultiHitCorpus:
        def search(self, text, *, top_k=5):
            return [("https://unrelated.com", unrelated), ("https://source.com/orig", near_dup)]

    ok, sim, urls = check_originality(draft, corpus=MultiHitCorpus(), threshold=0.25)
    assert ok is False
    assert sim == 1.0
    # Only the source(s) actually at/over threshold are reported, in `search()` order.
    assert urls == ["https://source.com/orig"]


def test_default_threshold_matches_settings_default():
    # `check_originality`'s default threshold must match `Settings.originality_threshold` (0.25)
    # so callers that omit `threshold` get the fail-closed default from the TRD, not a surprise.
    draft = "the quick brown fox jumps over the lazy dog every single morning without fail"

    class ExactMatchCorpus:
        def search(self, text, *, top_k=5):
            return [("https://source.com/orig", draft)]

    ok, sim, _ = check_originality(draft, corpus=ExactMatchCorpus())
    assert sim > 0.25
    assert ok is False


# --- WebCorpusSearch: real implementation, exercised only against a mocked transport -----------


@respx.mock
def test_web_corpus_search_maps_results():
    respx.get("https://search.example.com/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://a.com/post", "snippet": "some existing text"},
                    {"url": "https://b.com/post", "snippet": "more existing text"},
                ]
            },
        )
    )
    search = WebCorpusSearch(
        client=httpx.Client(), endpoint="https://search.example.com/v1/search", api_key="k"
    )
    hits = search.search("some draft text", top_k=2)
    assert hits == [
        ("https://a.com/post", "some existing text"),
        ("https://b.com/post", "more existing text"),
    ]


@respx.mock
def test_web_corpus_search_raises_on_http_error():
    respx.get("https://search.example.com/v1/search").mock(return_value=httpx.Response(500))
    search = WebCorpusSearch(
        client=httpx.Client(), endpoint="https://search.example.com/v1/search", api_key="k"
    )
    try:
        search.search("some draft text")
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected httpx.HTTPStatusError on a 5xx response")
