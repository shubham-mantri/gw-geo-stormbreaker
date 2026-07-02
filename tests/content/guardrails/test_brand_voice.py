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
