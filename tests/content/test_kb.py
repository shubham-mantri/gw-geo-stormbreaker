import pytest

from gw_geo.common.models import Fact
from gw_geo.content.kb import KnowledgeBase, PgVectorStore


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


def test_ground_scored_pairs_facts_with_similarity_score():
    kb = _kb()
    results = kb.ground_scored("what is your pricing price?", top_k=1)
    assert len(results) == 1
    fact, score = results[0]
    assert fact.id == "f1"
    assert isinstance(score, float)
    assert score > 0.0


def test_ground_scored_orders_most_similar_first():
    kb = _kb()
    results = kb.ground_scored("uptime guarantee uptime", top_k=3)
    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0][0].category == "claim"


def test_ground_scored_is_consistent_with_ground():
    # `ground` is now implemented in terms of `ground_scored` (T15); the Facts (and their order)
    # returned by each must always agree -- `ground` is just `ground_scored` with scores dropped.
    kb = _kb()
    query = "uptime guarantee uptime"
    assert [f.id for f in kb.ground(query, top_k=2)] == [
        f.id for f, _ in kb.ground_scored(query, top_k=2)
    ]


# --- PgVectorStore table-name guard (M5 review): the f-string table must stay a trusted identifier.


def test_pgvector_store_accepts_default_table():
    # Construction is I/O-free (psycopg import + connect are lazy), so this never touches a DB.
    store = PgVectorStore(database_url="postgresql://x", brand_id="b1")
    assert store is not None


def test_pgvector_store_rejects_unsafe_table_name():
    # Defense-in-depth: a table name that isn't a bare SQL identifier (here, an injection attempt)
    # must be refused at construction rather than interpolated into the f-string SQL.
    with pytest.raises(ValueError, match="safe SQL identifier"):
        PgVectorStore(
            database_url="postgresql://x",
            brand_id="b1",
            table="kb_fact_embedding; DROP TABLE tenant; --",
        )
