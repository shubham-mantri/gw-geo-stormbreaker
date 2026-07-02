from itertools import count

from gw_geo.common.models import Brand
from gw_geo.measurement.discover import build_prompt_set


class StubExpander:
    def expand(self, brand, seed_topics, size):
        return [
            {"text": f"best {t} for smb?", "intent_cluster": "evaluation"} for t in seed_topics
        ][:size]


def test_builds_and_caps():
    ids = count()
    brand = Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com")
    prompts = build_prompt_set(
        brand,
        ["crm", "helpdesk", "erp"],
        size=2,
        expander=StubExpander(),
        id_fn=lambda: f"p{next(ids)}",
    )
    assert len(prompts) == 2
    assert prompts[0].brand_id == "b1" and prompts[0].tenant_id == "t1"
    assert prompts[0].intent_cluster == "evaluation"


def test_dedupes():
    brand = Brand(id="b1", tenant_id="t1", name="Foo", domain="foo.com")

    class Dup:
        def expand(self, b, s, n):
            return [{"text": "x", "intent_cluster": "c"}] * 3

    assert len(build_prompt_set(brand, ["a"], 10, Dup(), id_fn=lambda: "p")) == 1
