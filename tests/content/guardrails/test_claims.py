import inspect
import math

from gw_geo.common.config import Settings
from gw_geo.common.models import Fact
from gw_geo.content.guardrails.claims import verify_claims
from gw_geo.content.kb import KnowledgeBase


class WordEmbedder:
    VOCAB = ["soc2", "price", "uptime", "revenue"]

    def embed(self, text):
        t = text.lower()
        return [1.0 if w in t else 0.0 for w in self.VOCAB]


class FakeStore:
    def __init__(self):
        self.rows = {}

    def upsert(self, id, vector, meta):
        self.rows[id] = (vector, meta)

    def query(self, vector, top_k):
        def cos(a, b):
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return 0.0 if na == 0 or nb == 0 else sum(x * y for x, y in zip(a, b)) / (na * nb)

        scored = [(i, cos(vector, v), m) for i, (v, m) in self.rows.items()]
        scored.sort(key=lambda r: r[1], reverse=True)
        return scored[:top_k]


def _kb():
    kb = KnowledgeBase(brand_id="b1", store=FakeStore(), embedder=WordEmbedder())
    kb.add_fact(
        Fact(id="f1", brand_id="b1", text="Acme is soc2 certified", category="certification")
    )
    kb.add_fact(Fact(id="f2", brand_id="b1", text="price starts at $29", category="pricing"))
    return kb


class StubExtractor:
    def __init__(self, claims):
        self._c = claims

    def extract_claims(self, text):
        return self._c


def test_grounded_claims_pass():
    ok, unver = verify_claims(
        "...",
        kb=_kb(),
        extractor=StubExtractor(["Acme is soc2 certified"]),
        sim_threshold=0.8,
    )
    assert ok is True and unver == []


def test_fabricated_claim_flagged():
    # "revenue tripled" is NOT in the KB -> unverified -> claims_ok False (the Athena failure,
    # prevented).
    ok, unver = verify_claims(
        "...",
        kb=_kb(),
        extractor=StubExtractor(["Acme revenue tripled last quarter"]),
        sim_threshold=0.8,
    )
    assert ok is False and "Acme revenue tripled last quarter" in unver


def test_mixed_claims_fail_closed():
    ok, unver = verify_claims(
        "...",
        kb=_kb(),
        extractor=StubExtractor(["Acme is soc2 certified", "Acme revenue tripled last quarter"]),
        sim_threshold=0.8,
    )
    assert ok is False and len(unver) == 1


# --- Additional coverage beyond the spec-mandated tests above ---------------------------------


def test_no_claims_trivially_passes():
    # Nothing extracted -> nothing left unverified -> ok, matching "claims_ok = (unverified == [])".
    ok, unver = verify_claims("...", kb=_kb(), extractor=StubExtractor([]), sim_threshold=0.8)
    assert ok is True and unver == []


def test_boundary_is_inclusive():
    # A claim embedding to exactly cosine similarity 1.0 against its supporting fact must pass
    # when sim_threshold is also 1.0 (>=, not strictly >), mirroring check_brand_voice's boundary.
    ok, unver = verify_claims(
        "...",
        kb=_kb(),
        extractor=StubExtractor(["Acme is soc2 certified"]),
        sim_threshold=1.0,
    )
    assert ok is True and unver == []


def test_unverified_claims_preserve_extraction_order():
    kb = _kb()
    claims = ["Acme revenue tripled last quarter", "Acme uptime is 99.999%"]
    ok, unver = verify_claims("...", kb=kb, extractor=StubExtractor(claims), sim_threshold=0.8)
    assert ok is False
    assert unver == claims


def test_default_threshold_matches_settings_default():
    # verify_claims' default sim_threshold must match Settings.claim_sim_threshold (0.8) so callers
    # that omit sim_threshold get the fail-closed default from the TRD, not a surprise.
    sig = inspect.signature(verify_claims)
    assert sig.parameters["sim_threshold"].default == Settings().claim_sim_threshold
