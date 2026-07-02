from gw_geo.common.models import Fact
from gw_geo.content.kb import KnowledgeBase


class FakeStore:
    def __init__(self):
        self.rows = {}

    def upsert(self, id, vector, meta):
        self.rows[id] = (vector, meta)

    def query(self, vector, top_k):
        scored = [(i, sum(a * b for a, b in zip(vector, v)), m) for i, (v, m) in self.rows.items()]
        scored.sort(key=lambda r: r[1], reverse=True)
        return scored[:top_k]


class WordEmbedder:
    VOCAB = ["price", "uptime", "soc2"]

    def embed(self, text):
        t = text.lower()
        return [1.0 if w in t else 0.0 for w in self.VOCAB]


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
