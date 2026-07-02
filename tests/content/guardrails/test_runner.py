from gw_geo.common.config import get_settings
from gw_geo.common.models import ContentDraft
from gw_geo.content.guardrails.runner import GuardrailThresholds, run_guardrails


def _draft():
    return ContentDraft(
        id="c1",
        tenant_id="t1",
        brand_id="b1",
        title="T",
        body_markdown="Acme is soc2 certified. price starts at $29.",
    )


class CleanCorpus:
    """Nothing similar in the corpus -> original."""

    def search(self, text, *, top_k=5):
        return []


class DupCorpus:
    """The closest corpus hit is the draft itself -> plagiarized."""

    def search(self, text, *, top_k=5):
        return [("https://o", _draft().body_markdown)]


class GroundedExtractor:
    def extract_claims(self, text):
        return ["Acme is soc2 certified"]


class UngroundedExtractor:
    def extract_claims(self, text):
        return ["Acme revenue tripled"]


class GoodVoice:
    def score(self, text, profile):
        return {"score": 0.95, "violations": []}


class BadVoice:
    def score(self, text, profile):
        return {"score": 0.3, "violations": ["too salesy"]}


def _kb():
    from gw_geo.common.models import Fact
    from gw_geo.content.kb import KnowledgeBase
    from tests.content.guardrails.test_claims import FakeStore, WordEmbedder

    kb = KnowledgeBase(brand_id="b1", store=FakeStore(), embedder=WordEmbedder())
    kb.add_fact(
        Fact(id="f1", brand_id="b1", text="Acme is soc2 certified", category="certification")
    )
    return kb


def test_all_pass():
    r = run_guardrails(
        _draft(),
        kb=_kb(),
        corpus=CleanCorpus(),
        extractor=GroundedExtractor(),
        voice_scorer=GoodVoice(),
        voice_profile={},
    )
    assert r.originality_ok and r.claims_ok and r.brand_voice_ok and r.passed is True


def test_any_failure_blocks():
    # Plagiarism fails -> passed False even if the rest pass.
    r = run_guardrails(
        _draft(),
        kb=_kb(),
        corpus=DupCorpus(),
        extractor=GroundedExtractor(),
        voice_scorer=GoodVoice(),
        voice_profile={},
    )
    assert r.originality_ok is False and r.passed is False


def test_ungrounded_claim_blocks():
    r = run_guardrails(
        _draft(),
        kb=_kb(),
        corpus=CleanCorpus(),
        extractor=UngroundedExtractor(),
        voice_scorer=GoodVoice(),
        voice_profile={},
    )
    assert r.claims_ok is False and r.passed is False
    assert "Acme revenue tripled" in r.unverified_claims


# --- Additional coverage beyond the spec-mandated tests above ---------------------------------


def test_off_voice_blocks():
    # Off-brand-voice fails -> passed False even if originality/claims are clean.
    r = run_guardrails(
        _draft(),
        kb=_kb(),
        corpus=CleanCorpus(),
        extractor=GroundedExtractor(),
        voice_scorer=BadVoice(),
        voice_profile={},
    )
    assert r.brand_voice_ok is False and r.passed is False
    assert r.brand_voice_score == 0.3


def test_scores_are_plumbed_through_report():
    # originality_score/brand_voice_score must be the actual computed values, not just their
    # pass/fail booleans -- guards against the score fields being swapped or dropped.
    r = run_guardrails(
        _draft(),
        kb=_kb(),
        corpus=CleanCorpus(),
        extractor=GroundedExtractor(),
        voice_scorer=GoodVoice(),
        voice_profile={},
    )
    assert r.originality_score == 0.0
    assert r.brand_voice_score == 0.95


def test_default_thresholds_match_settings():
    # GuardrailThresholds' field defaults must match Settings' M3 guardrail defaults (T01),
    # so a caller that never overrides `thresholds` still gets the fail-closed config values.
    settings = get_settings()
    defaults = GuardrailThresholds()
    assert defaults.originality == settings.originality_threshold
    assert defaults.claim_sim == settings.claim_sim_threshold
    assert defaults.brand_voice == settings.brand_voice_min


def test_custom_originality_threshold_is_honored():
    # The corpus hit shares exactly one 5-word shingle with the draft (jaccard ~= 0.0909): below
    # the default 0.25 threshold (passes), but at/above a stricter custom threshold (blocked).
    # This proves `thresholds.originality` is actually threaded into `check_originality`, not
    # silently replaced by its own hardcoded default.
    class BorderlineCorpus:
        def search(self, text, *, top_k=5):
            return [("https://p", "zeta yotta xray whiskey acme is soc2 certified price "
                                   "tango foxtrot bravo")]

    lenient = run_guardrails(
        _draft(), kb=_kb(), corpus=BorderlineCorpus(), extractor=GroundedExtractor(),
        voice_scorer=GoodVoice(), voice_profile={},
        thresholds=GuardrailThresholds(originality=0.05),
    )
    assert lenient.originality_ok is False and lenient.passed is False

    default = run_guardrails(
        _draft(), kb=_kb(), corpus=BorderlineCorpus(), extractor=GroundedExtractor(),
        voice_scorer=GoodVoice(), voice_profile={},
    )
    assert default.originality_ok is True and default.passed is True


def test_custom_claim_sim_threshold_is_honored():
    # This claim embeds to cosine similarity ~=0.7071 against its one supporting fact: below the
    # default 0.8 sim_threshold (unverified), but above a looser custom threshold (verified).
    # Proves `thresholds.claim_sim` is threaded into `verify_claims`.
    claim = "Acme is soc2 certified and price is fair"

    class BorderlineExtractor:
        def extract_claims(self, text):
            return [claim]

    strict = run_guardrails(
        _draft(), kb=_kb(), corpus=CleanCorpus(), extractor=BorderlineExtractor(),
        voice_scorer=GoodVoice(), voice_profile={},
    )
    assert strict.claims_ok is False and claim in strict.unverified_claims

    lenient = run_guardrails(
        _draft(), kb=_kb(), corpus=CleanCorpus(), extractor=BorderlineExtractor(),
        voice_scorer=GoodVoice(), voice_profile={},
        thresholds=GuardrailThresholds(claim_sim=0.7),
    )
    assert lenient.claims_ok is True and lenient.passed is True


def test_custom_brand_voice_threshold_is_honored():
    # GoodVoice always scores 0.95: passes the default 0.7 min_score, but a stricter custom
    # threshold above 0.95 must block it. Proves `thresholds.brand_voice` is threaded into
    # `check_brand_voice`.
    r = run_guardrails(
        _draft(), kb=_kb(), corpus=CleanCorpus(), extractor=GroundedExtractor(),
        voice_scorer=GoodVoice(), voice_profile={},
        thresholds=GuardrailThresholds(brand_voice=0.99),
    )
    assert r.brand_voice_ok is False and r.passed is False
