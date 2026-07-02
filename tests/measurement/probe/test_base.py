import pytest
from gw_geo.common.models import ProbeResult
from gw_geo.measurement.probe import base

class FakeAdapter:
    name = "fake"; supports_citations = True
    async def probe(self, prompt, *, geo="us", persona=None):
        return ProbeResult(engine="fake", answer_text="hi", cited_urls=["https://x.com"])

def test_register_and_get():
    base.clear_registry(); a = FakeAdapter(); base.register(a)
    assert base.get_adapter("fake") is a
    assert isinstance(a, base.EngineAdapter)

def test_duplicate_name_rejected():
    base.clear_registry(); base.register(FakeAdapter())
    with pytest.raises(ValueError):
        base.register(FakeAdapter())

def test_unknown_adapter_raises():
    base.clear_registry()
    with pytest.raises(KeyError):
        base.get_adapter("nope")
